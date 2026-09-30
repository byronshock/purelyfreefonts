#!/usr/bin/env bash
# ops/deploy/deploy_key_tests.sh KEY USER [HOST]: prove a deploy key can do nothing but deploy.
#
#   KEY   the private key file (run right after generating it, before gh secret set)
#   USER  deploy or deploy-staging
#   HOST  where to connect (default: $TFF_DEPLOY_HOST, else the ssh alias tff)
#
# Every forbidden action in design-m2 §5 must fail or be rejected: a shell or any command
# other than tff-receive's verbs, a PTY, port/socket/agent forwarding, sftp, scp, rsync,
# 'upload ../../etc', malicious tars, and SSH_ORIGINAL_COMMAND sent with SetEnv/SendEnv.
# Nothing is ever activated: the tars go to a fake release that is refused (a plan leaves a
# small manifest in incoming/, cleared after a day).
#
# Server-side checks (sshd -T, Byron's login, the other environment's directory, byron's
# home) run over Byron's own login, the ssh alias in $TFF_ADMIN_HOST (default tff); set
# TFF_ADMIN_HOST= (empty) to skip them, as the ssh-audit workflow does.
#
# TFF_KEYTEST_SSH_OPTS adds client options to every ssh, sftp, scp and rsync call, e.g.
# "-o UserKnownHostsFile=FILE" in Actions, or "-F /dev/null -o Port=N -o User=U" to try the
# script against a local test sshd.
#
# Exit status: 0 when every check passes, 1 otherwise, 2 for usage.
set -uo pipefail

[[ $# -ge 2 && $# -le 3 ]] || {
	sed -n '2,/^set -uo/{/^set -uo/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}" >&2
	exit 2
}
key=$1 user=$2 host=${3:-${TFF_DEPLOY_HOST:-tff}}
admin=${TFF_ADMIN_HOST-tff}
case $user in
deploy) env=production env_dir=prod other_env=staging other_dir=staging ;;
deploy-staging) env=staging env_dir=staging other_env=production other_dir=prod ;;
*) echo "USER must be deploy or deploy-staging" >&2; exit 2 ;;
esac
[[ -r $key ]] || { echo "cannot read $key" >&2; exit 2; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/tff-keytest.XXXXXX")
bg_pids=()
cleanup() {
	local pid
	for pid in "${bg_pids[@]}"; do kill "$pid" 2>/dev/null; done
	rm -rf -- "$tmp"
}
trap cleanup EXIT

RECEIVE=/usr/local/sbin/tff-receive
FAKE_SHA=0000000000000000000000000000000000000bad
read -r -a extra_opts <<<"${TFF_KEYTEST_SSH_OPTS:-}"
# ssh keeps the first value of an option: a call that needs another LogLevel (sshd's
# "administratively prohibited" shows only at INFO) must put it before "${OPTS[@]}".
OPTS=(-i "$key" -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes
	-o ControlMaster=no -o ControlPath=none -o ConnectTimeout=20 -o ForwardAgent=no
	-o ForwardX11=no -o LogLevel=ERROR "${extra_opts[@]}")
target="$user@$host"

passed=0 failed=0
pass() {
	printf 'PASS  %s\n' "$1"
	passed=$((passed + 1))
}
fail() {
	printf 'FAIL  %s\n' "$1"
	[[ -n ${2:-} ]] && printf '        %s\n' "${2//$'\n'/$'\n        '}"
	failed=$((failed + 1))
}

# run [--in FILE] CMD...: run with stdin from FILE (default /dev/null); sets rc, out, err.
run() {
	local input=/dev/null
	if [[ $1 == --in ]]; then input=$2; shift 2; fi
	out=$(timeout 60 "$@" <"$input" 2>"$tmp/stderr")
	rc=$?
	err=$(<"$tmp/stderr")
}

# A local port for a forwarding check, below Linux's ephemeral range (32768 and up), where
# the system hands out ports to other programs' servers and outgoing connections: on a busy
# CI runner a port up there was once already taken, and the -L check failed on the bind.
free_port() { echo $((20000 + RANDOM % 12000)); }
# listening PORT SECONDS: wait until a background ssh listens on 127.0.0.1:PORT. A fixed
# sleep raced a slow login on a loaded machine: the check then saw no tunnel at all.
listening() {
	local i
	for ((i = 0; i < $2 * 10; i++)); do
		(exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
		sleep 0.1
	done
	return 1
}
# logged FILE TEXT SECONDS: wait until FILE holds TEXT (ssh logs a refusal after the fact).
logged() {
	local i
	for ((i = 0; i < $3 * 10; i++)); do
		grep -q "$2" "$1" 2>/dev/null && return 0
		sleep 0.1
	done
	return 1
}

echo "Deploy key tests: $user ($env) at $host"

# --- the key works, and only for tff-receive ------------------------------------------------
run ssh "${OPTS[@]}" -T "$target" status
if [[ $rc == 0 && $out == *"environment: $env"* ]]; then
	pass "the key logs in and 'status' answers for $env"
else
	fail "the key logs in and 'status' answers for $env" "rc=$rc $err"
fi

run ssh "${OPTS[@]}" -T "$target"
if [[ $rc == 2 && $err == *usage:* ]]; then pass "no command: usage, exit 2"; else fail "no command: usage, exit 2" "rc=$rc $err"; fi

for cmd in bash sh 'cat /etc/passwd' id 'ls -la /' 'status; id' 'status && id' \
	"$RECEIVE $other_env status" 'upload ../../etc' "upload $FAKE_SHA ../../etc" \
	'rsync --server -vlogDtpre.iLsfxCIvu . /tmp' 'scp -t /tmp' 'internal-sftp' \
	'/usr/lib/openssh/sftp-server'; do
	run ssh "${OPTS[@]}" -T "$target" "$cmd"
	if [[ $rc == 2 && $out != *root:* && $out != *uid=* ]]; then
		pass "command '$cmd' refused (exit 2)"
	else
		fail "command '$cmd' refused (exit 2)" "rc=$rc out=${out:0:200} $err"
	fi
done

# --- no PTY, no forwarding ------------------------------------------------------------------
run ssh -o LogLevel=INFO "${OPTS[@]}" -tt "$target" status
if [[ $err == *"PTY allocation request failed"* ]]; then pass "ssh -tt: PTY refused"; else fail "ssh -tt: PTY refused" "rc=$rc $err"; fi

port=$(free_port)
ssh -o LogLevel=INFO "${OPTS[@]}" -o ExitOnForwardFailure=yes -N \
	-L "127.0.0.1:$port:127.0.0.1:22" "$target" 2>"$tmp/local.err" &
bg_pids+=($!)
listening "$port" 30 || true
banner=$(timeout 8 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port && head -c 7 <&3" 2>/dev/null)
[[ $banner == SSH-* ]] || logged "$tmp/local.err" 'administratively prohibited' 10 || true
kill "${bg_pids[-1]}" 2>/dev/null
wait "${bg_pids[-1]}" 2>/dev/null
# No banner alone would also pass if ssh never connected: sshd must say it refused.
if [[ $banner != SSH-* ]] && grep -q 'administratively prohibited' "$tmp/local.err"; then
	pass "ssh -L: tunnel refused (administratively prohibited)"
else
	fail "ssh -L: tunnel refused (administratively prohibited)" "banner=${banner:-none} $(<"$tmp/local.err")"
fi

port=$(free_port)
run ssh -o LogLevel=INFO "${OPTS[@]}" -o ExitOnForwardFailure=yes -N \
	-R "127.0.0.1:$port:127.0.0.1:22" "$target"
if [[ $rc != 0 && $rc != 124 && $err == *forwarding* ]]; then
	pass "ssh -R: remote forwarding refused"
else
	fail "ssh -R: remote forwarding refused" "rc=$rc $err"
fi

port=$(free_port)
ssh -o LogLevel=INFO "${OPTS[@]}" -N -D "127.0.0.1:$port" "$target" 2>"$tmp/dynamic.err" &
bg_pids+=($!)
listening "$port" 30 || true
socks=$(curl -s --max-time 8 --socks5-hostname "127.0.0.1:$port" telnet://127.0.0.1:22 </dev/null 2>/dev/null | head -c 7)
[[ $socks == SSH-* ]] || logged "$tmp/dynamic.err" 'administratively prohibited' 10 || true
kill "${bg_pids[-1]}" 2>/dev/null
wait "${bg_pids[-1]}" 2>/dev/null
if [[ $socks != SSH-* ]] && grep -q 'administratively prohibited' "$tmp/dynamic.err"; then
	pass "ssh -D: SOCKS tunnel refused (administratively prohibited)"
else
	fail "ssh -D: SOCKS tunnel refused (administratively prohibited)" "got ${socks:-nothing} $(<"$tmp/dynamic.err")"
fi

# -A: ssh sends the agent request without asking for a reply, so a refusal can't be seen
# from the client, and offering the operator's real agent to test it would be unsafe. The
# sshd -T checks below (allowagentforwarding no, disableforwarding yes) cover it.
echo "NOTE  ssh -A: checked through sshd -T (a refused agent request is silent)"

# --- file transfer tools --------------------------------------------------------------------
run --in <(printf 'ls /\nget /etc/passwd %s\n' "$tmp/sftp.out") sftp "${OPTS[@]}" -b - "$target"
if [[ $rc != 0 && ! -s $tmp/sftp.out ]]; then pass "sftp refused"; else fail "sftp refused" "rc=$rc $err"; fi

run scp "${OPTS[@]}" "$target:/etc/passwd" "$tmp/scp.out"
if [[ $rc != 0 && ! -s $tmp/scp.out ]]; then pass "scp (sftp mode) refused"; else fail "scp (sftp mode) refused" "rc=$rc $err"; fi

run scp -O "${OPTS[@]}" "$target:/etc/passwd" "$tmp/scp-legacy.out"
if [[ $rc != 0 && ! -s $tmp/scp-legacy.out ]]; then pass "scp -O (legacy) refused"; else fail "scp -O (legacy) refused" "rc=$rc $err"; fi

printf 'x\n' >"$tmp/upload.txt"
run scp "${OPTS[@]}" "$tmp/upload.txt" "$target:/tmp/tff-keytest-scp.txt"
if [[ $rc != 0 ]]; then pass "scp upload refused"; else fail "scp upload refused" "rc=$rc $err"; fi

rsync_ssh="ssh$(printf ' %q' "${OPTS[@]}")"
run rsync -e "$rsync_ssh" "$target:/etc/passwd" "$tmp/rsync.out"
if [[ $rc != 0 && ! -s $tmp/rsync.out ]]; then pass "rsync -e ssh refused"; else fail "rsync -e ssh refused" "rc=$rc $err"; fi

# --- environment tricks ---------------------------------------------------------------------
run ssh "${OPTS[@]}" -o "SetEnv=SSH_ORIGINAL_COMMAND=status" -T "$target"
if [[ $rc == 2 ]]; then pass "SetEnv SSH_ORIGINAL_COMMAND ignored"; else fail "SetEnv SSH_ORIGINAL_COMMAND ignored" "rc=$rc $err"; fi

run env SSH_ORIGINAL_COMMAND=status ssh "${OPTS[@]}" -o SendEnv=SSH_ORIGINAL_COMMAND -T "$target"
if [[ $rc == 2 ]]; then pass "SendEnv SSH_ORIGINAL_COMMAND ignored"; else fail "SendEnv SSH_ORIGINAL_COMMAND ignored" "rc=$rc $err"; fi

run ssh "${OPTS[@]}" -o "SetEnv=PYTHONSTARTUP=/etc/passwd PYTHONPATH=/tmp LD_PRELOAD=/tmp/x.so" -T "$target" status
if [[ $rc == 0 ]]; then pass "SetEnv PYTHON*/LD_PRELOAD: no effect"; else fail "SetEnv PYTHON*/LD_PRELOAD: no effect" "rc=$rc $err"; fi

# --- malicious uploads ----------------------------------------------------------------------
if python3 "$here/badtars.py" "$tmp/tars" "$FAKE_SHA"; then
	run --in "$tmp/tars/manifest.json" ssh "${OPTS[@]}" -T "$target" "plan $FAKE_SHA"
	if [[ $rc == 0 ]]; then
		while IFS=$'\t' read -r name expect; do
			run --in "$tmp/tars/$name.tar" ssh "${OPTS[@]}" -T "$target" "upload $FAKE_SHA"
			if [[ $rc == 3 && $err == *"$expect"* ]]; then
				pass "malicious tar '$name' refused ($expect)"
			else
				fail "malicious tar '$name' refused ($expect)" "rc=$rc $err"
			fi
		done <"$tmp/tars/cases.tsv"
	else
		fail "plan for the fake release" "rc=$rc $err"
	fi
	run ssh "${OPTS[@]}" -T "$target" "activate $FAKE_SHA"
	# 3: no such release; 4: the environment is held by "rollback --hold".
	if [[ $rc == 3 || $rc == 4 ]]; then pass "activate of the refused release fails (exit $rc)"; else fail "activate of the refused release fails" "rc=$rc $err"; fi
else
	fail "generate malicious tars" "python3 $here/badtars.py failed"
fi

# --- server-side checks, over Byron's login -------------------------------------------------
if [[ -z $admin ]]; then
	echo "SKIP  server-side checks (TFF_ADMIN_HOST is empty)"
else
	run ssh -o ControlPath=none -o BatchMode=yes "$admin" true
	if [[ $rc == 0 ]]; then pass "Byron's login still works"; else fail "Byron's login still works" "rc=$rc $err"; fi

	run ssh -o BatchMode=yes "$admin" sudo -n sshd -T -C "user=$user,host=x,addr=192.0.2.1"
	for want in "forcecommand $RECEIVE $env" "permittty no" "disableforwarding yes" \
		"allowtcpforwarding no" "allowagentforwarding no" "allowstreamlocalforwarding no" \
		"x11forwarding no" "permittunnel no" "permituserrc no" "passwordauthentication no" \
		"kbdinteractiveauthentication no" "authenticationmethods publickey"; do
		if grep -qix "$want" <<<"$out"; then pass "sshd -T $user: $want"; else fail "sshd -T $user: $want" "rc=$rc $err"; fi
	done
	run ssh -o BatchMode=yes "$admin" sudo -n sshd -T -C "user=byron,host=x,addr=192.0.2.1"
	if grep -qix 'allowusers byron' <<<"$out" && grep -qix 'forcecommand none' <<<"$out"; then
		pass "sshd -T byron: allowed, no forced command"
	else
		fail "sshd -T byron: allowed, no forced command" "rc=$rc $err"
	fi

	run ssh -o BatchMode=yes "$admin" "sudo -n -u $user touch /srv/trulyfreefonts/$other_dir/tff-keytest 2>&1; echo rc=\$?"
	if [[ $out == *"Permission denied"* && $out != *rc=0* ]]; then
		pass "$user cannot write /srv/trulyfreefonts/$other_dir (EACCES)"
	else
		fail "$user cannot write /srv/trulyfreefonts/$other_dir" "$out $err"
	fi
	run ssh -o BatchMode=yes "$admin" "sudo -n -u $user $RECEIVE $other_env status"
	if [[ $rc == 2 ]]; then pass "$user cannot run tff-receive $other_env"; else fail "$user cannot run tff-receive $other_env" "rc=$rc $err"; fi
	run ssh -o BatchMode=yes "$admin" "sudo -n -u $user ls /home/byron 2>&1; echo rc=\$?"
	if [[ $out == *"Permission denied"* ]]; then
		pass "$user cannot read /home/byron (nor the snapshot store)"
	else
		fail "$user cannot read /home/byron" "$out"
	fi
	run ssh -o BatchMode=yes "$admin" "sudo -n find /srv /tmp /etc /var/lib -xdev -name 'tff-keytest*' -print 2>/dev/null"
	if [[ -z $out ]]; then pass "no tff-keytest file escaped anywhere"; else fail "no tff-keytest file escaped" "$out"; fi
	run ssh -o BatchMode=yes "$admin" "test ! -e /srv/trulyfreefonts/$env_dir/releases/$FAKE_SHA && echo absent"
	if [[ $out == absent ]]; then pass "no release was created for the fake commit"; else fail "no release for the fake commit" "$out $err"; fi
fi

echo
echo "$passed passed, $failed failed"
[[ $failed == 0 ]]
