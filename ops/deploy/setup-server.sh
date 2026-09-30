#!/usr/bin/env bash
# ops/deploy/setup-server.sh: set the server up for deploys (design-m2 §5). Idempotent.
#
# Copy ops/deploy/ to the server and run it there as root:
#   scp -r ops/deploy tff:/tmp/tff-deploy
#   ssh tff sudo bash /tmp/tff-deploy/setup-server.sh stage-a [--key-production F.pub] [--key-staging F.pub]
#   ssh tff sudo bash /tmp/tff-deploy/setup-server.sh stage-b --control-connection-open
#   ssh tff sudo bash /tmp/tff-deploy/setup-server.sh stage-b-undo
#
# stage-a (no sshd change; safe): the system users deploy and deploy-staging (shell /bin/sh,
#   locked password, root-owned home under /var/lib, no sudo, no other groups); the
#   directories /srv/trulyfreefonts/{prod,staging} with releases/ and incoming/; tff-receive in
#   /usr/local/sbin; the root-owned /etc/ssh/authorized_keys/{deploy,deploy-staging}, holding
#   'restrict,command="/usr/local/sbin/tff-receive ENV" KEY' when --key-* gives a public key.
#   It never changes /srv/trulyfreefonts itself or public/: the migration does that.
#   Test afterwards with: sudo -u deploy /usr/local/sbin/tff-receive production status
#
# stage-b (lockout risk): adds deploy and deploy-staging to AllowUsers in 00-hardening.conf
#   (backup .bak), installs 10-deploy.conf, then sshd -t and sshd -T checks; only if they pass
#   does it reload ssh, else it restores the old files. Hold a master connection open first
#   (ops/deploy/README.md, "Lockout precautions") and pass --control-connection-open.
#
# stage-b-undo: restores 00-hardening.conf from .bak, removes 10-deploy.conf, reloads ssh.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRV=/srv/trulyfreefonts
RECEIVE=/usr/local/sbin/tff-receive
KEYDIR=/etc/ssh/authorized_keys
SSHD_DIR=/etc/ssh/sshd_config.d
HARDENING=$SSHD_DIR/00-hardening.conf
DROPIN=$SSHD_DIR/10-deploy.conf
# user:environment:directory:home
ACCOUNTS=(
	"deploy:production:prod:/var/lib/tff-deploy"
	"deploy-staging:staging:staging:/var/lib/tff-deploy-staging"
)

die() {
	printf 'setup-server.sh: %s\n' "$*" >&2
	exit 1
}
say() { printf '==> %s\n' "$*"; }
usage() {
	sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}" >&2
	exit 2
}

need_root() { [[ $(id -u) == 0 ]] || die "run as root (sudo bash $0 ...)"; }

setup_user() {
	local user=$1 env=$2 home=$3
	if ! getent passwd "$user" >/dev/null; then
		say "adding user $user"
		useradd --system --user-group --shell /bin/sh --home-dir "$home" --no-create-home \
			--comment "trulyfreefonts deploy ($env)" "$user"
	fi
	# ForceCommand runs through the login shell, so it must be a real shell, not nologin.
	usermod --shell /bin/sh --home "$home" --groups '' "$user"
	usermod --password '!' "$user" # locked: no password login, keys only
	install -d -o root -g root -m 755 "$home"
	if [[ $(id -Gn "$user") != "$user" ]]; then
		die "$user is in other groups: $(id -Gn "$user")"
	fi
	# Captured first: with pipefail, grep -q closing the pipe early could hide a match.
	local rights
	rights=$(sudo -l -U "$user" 2>/dev/null || true)
	if [[ $rights == *"may run"* ]]; then
		die "$user may run sudo; remove that rule"
	fi
}

setup_dirs() {
	local user=$1 dir=$2
	if [[ ! -d $SRV ]]; then
		install -d -o root -g root -m 755 "$SRV"
	fi
	install -d -o "$user" -g "$user" -m 755 "$SRV/$dir" "$SRV/$dir/releases"
	install -d -o "$user" -g "$user" -m 700 "$SRV/$dir/incoming"
	local f
	for f in history.log .lock; do
		if [[ ! -e $SRV/$dir/$f ]]; then
			install -o "$user" -g "$user" -m 644 /dev/null "$SRV/$dir/$f"
		fi
	done
}

install_key() { # install_key USER ENV [PUBKEY_FILE]
	local user=$1 env=$2 pub=${3:-} dest=$KEYDIR/$1 line tmp
	install -d -o root -g root -m 755 "$KEYDIR"
	if [[ -z $pub ]]; then
		if [[ ! -e $dest ]]; then
			install -o root -g root -m 644 /dev/null "$dest"
		fi
		return
	fi
	[[ -r $pub ]] || die "cannot read $pub"
	[[ $(grep -cv '^[[:space:]]*$' "$pub") == 1 ]] || die "$pub must hold exactly one key"
	line=$(grep -v '^[[:space:]]*$' "$pub")
	[[ $line == ssh-ed25519\ * ]] || die "$pub is not an ssh-ed25519 public key"
	ssh-keygen -l -f "$pub" >/dev/null || die "$pub is not a valid public key"
	tmp=$(mktemp "$KEYDIR/.$user.XXXXXX")
	printf 'restrict,command="%s %s" %s\n' "$RECEIVE" "$env" "$line" >"$tmp"
	chown root:root "$tmp"
	chmod 644 "$tmp"
	mv -f "$tmp" "$dest"
	say "key for $user: $(ssh-keygen -l -f "$dest" | awk '{print $2, $3}')"
}

stage_a() {
	local key_production="" key_staging=""
	while [[ $# -gt 0 ]]; do
		case $1 in
		--key-production) [[ $# -ge 2 ]] || usage; key_production=$2; shift ;;
		--key-staging) [[ $# -ge 2 ]] || usage; key_staging=$2; shift ;;
		*) usage ;;
		esac
		shift
	done
	need_root
	# The interpreter in tff-receive's #! line, not whichever python3 is first on PATH.
	/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
		die "tff-receive needs /usr/bin/python3 at 3.12 or later"
	[[ -f $here/tff-receive ]] || die "tff-receive not found next to this script"

	say "installing $RECEIVE"
	install -o root -g root -m 755 "$here/tff-receive" "$RECEIVE"

	local entry user env dir home key
	for entry in "${ACCOUNTS[@]}"; do
		IFS=: read -r user env dir home <<<"$entry"
		setup_user "$user" "$env" "$home"
		setup_dirs "$user" "$dir"
		key=$key_production
		[[ $env == staging ]] && key=$key_staging
		install_key "$user" "$env" "$key"
		say "checking: sudo -u $user $RECEIVE $env status"
		(cd / && sudo -u "$user" "$RECEIVE" "$env" status >/dev/null) ||
			die "tff-receive $env status failed as $user"
	done
	say "stage A done. The deploy users can't log in until stage B changes sshd."
}

sshd_checks() {
	sshd -t || return 1
	local out
	out=$(sshd -T -C user=byron,host=x,addr=192.0.2.1) || return 1
	grep -qix 'allowusers byron' <<<"$out" || {
		echo "byron would be locked out" >&2
		return 1
	}
	grep -qix 'forcecommand none' <<<"$out" || {
		echo "byron would get a forced command" >&2
		return 1
	}
	local entry user env dir home
	for entry in "${ACCOUNTS[@]}"; do
		IFS=: read -r user env dir home <<<"$entry"
		out=$(sshd -T -C "user=$user,host=x,addr=192.0.2.1") || return 1
		local want
		for want in "allowusers $user" "forcecommand $RECEIVE $env" "permittty no" \
			"disableforwarding yes" "allowtcpforwarding no" "allowagentforwarding no" \
			"passwordauthentication no" "authenticationmethods publickey" \
			"authorizedkeysfile $KEYDIR/$user"; do
			grep -qix "$want" <<<"$out" || {
				echo "sshd -T for $user lacks: $want" >&2
				return 1
			}
		done
	done
}

stage_b() {
	[[ ${1:-} == --control-connection-open ]] ||
		die "open a control connection first (README), then pass --control-connection-open"
	need_root
	[[ -f $HARDENING ]] || die "$HARDENING not found"
	[[ -f $here/sshd-deploy.conf ]] || die "sshd-deploy.conf not found next to this script"
	local had_dropin=0 changed=0 saved
	[[ -e $DROPIN ]] && had_dropin=1
	saved=$(mktemp -d)
	cp -p "$HARDENING" "$saved/hardening"
	[[ $had_dropin == 1 ]] && cp -p "$DROPIN" "$saved/dropin"

	if grep -qx 'AllowUsers byron deploy deploy-staging' "$HARDENING"; then
		say "AllowUsers already lists the deploy users"
	elif grep -qx 'AllowUsers byron' "$HARDENING"; then
		cp -p "$HARDENING" "$HARDENING.bak" # the pre-deploy-users file, kept for stage-b-undo
		sed -i 's/^AllowUsers byron$/AllowUsers byron deploy deploy-staging/' "$HARDENING"
		changed=1
		say "AllowUsers: byron deploy deploy-staging"
	else
		die "unexpected AllowUsers line in $HARDENING; edit it by hand"
	fi
	install -o root -g root -m 644 "$here/sshd-deploy.conf" "$DROPIN"

	if ! sshd_checks; then
		say "checks failed: restoring the previous sshd files"
		[[ $changed == 1 ]] && cp -p "$saved/hardening" "$HARDENING"
		if [[ $had_dropin == 1 ]]; then cp -p "$saved/dropin" "$DROPIN"; else rm -f "$DROPIN"; fi
		rm -rf "$saved"
		sshd -t && die "sshd config restored, nothing reloaded"
		die "sshd config restored but sshd -t still fails: fix it before any reload"
	fi
	rm -rf "$saved"
	systemctl reload ssh
	say "ssh reloaded. From the laptop, before closing the control connection:"
	say "  ssh -o ControlPath=none tff true    (Byron's login still works)"
	say "  ops/deploy/deploy_key_tests.sh KEY deploy-staging"
}

stage_b_undo() {
	need_root
	[[ -f $HARDENING.bak ]] || die "no $HARDENING.bak to restore"
	cp -p "$HARDENING.bak" "$HARDENING"
	rm -f "$DROPIN"
	sshd -t || die "sshd -t fails after the undo; fix it before any reload"
	systemctl reload ssh
	say "restored $HARDENING, removed $DROPIN, reloaded ssh"
}

[[ $# -ge 1 ]] || usage
case $1 in
stage-a) shift; stage_a "$@" ;;
stage-b) shift; stage_b "$@" ;;
stage-b-undo) shift; stage_b_undo "$@" ;;
*) usage ;;
esac
