#!/usr/bin/env bash
# ops/deploy.sh: deploy purelyfreefonts.com from the laptop. The normal path is the GitHub
# Actions workflow (.github/workflows/deploy.yml); this is its fallback (M2-D6).
#
#   ops/deploy.sh production|staging [--commit SHA] [--fast] [--any-commit]
#   ops/deploy.sh production|staging --dir DIR [--commit SHA] [--no-live] [--any-commit]
#   ops/deploy.sh rollback production|staging [SHA] [--hold]
#   ops/deploy.sh unhold production|staging
#   ops/deploy.sh status production|staging
#
# A deploy builds the commit (default: HEAD) in a temporary git worktree: uv sync,
# tff-site fetch-fonts, build (staging with --drafts), check, the site tests (skipped with
# --fast), then pack.
# It uploads only the files the server lacks, switches the site in one step, and ends with
# the live test (tests/live) against the site. --dir DIR deploys a directory as it is (the
# one-time migration of the stub in public/): it adds version.txt when missing and checks
# only the live version.txt, or nothing with --no-live.
#
# Everything on the server goes through tff-receive as the deploy user, over Byron's own SSH
# login: ssh tff sudo -n -u deploy /usr/local/sbin/tff-receive production VERB ...
# No deploy key is kept on the laptop. Production takes only commits on origin/main unless
# --any-commit. See ops/deploy/README.md.
#
# Environment: TFF_SSH_HOST (default tff).
set -euo pipefail

HOST=${TFF_SSH_HOST:-tff}
RECEIVE=/usr/local/sbin/tff-receive
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

die() {
	printf 'deploy.sh: %s\n' "$*" >&2
	exit 1
}
say() { printf '==> %s\n' "$*" >&2; }
usage() {
	sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}" >&2
	exit 2
}

env_user() {
	case $1 in
	production) echo deploy ;;
	staging) echo deploy-staging ;;
	*) usage ;;
	esac
}

site_url() {
	case $1 in
	production) echo https://purelyfreefonts.com ;;
	staging) echo https://staging.purelyfreefonts.com ;;
	*) usage ;;
	esac
}

# receive ENV VERB [ARGS...]: run tff-receive as the environment's deploy user; stdin and
# stdout pass through.
receive() {
	local env=$1 user
	shift
	user=$(env_user "$env")
	ssh -o BatchMode=yes "$HOST" sudo -n -u "$user" "$RECEIVE" "$env" "$@"
}

live_version() { # live_version ENV: the commit= line the live site serves
	curl -fsS --max-time 15 -H 'Cache-Control: no-cache' "$(site_url "$1")/version.txt" |
		sed -n 's/^commit=//p'
}

cmd_status() {
	[[ $# -eq 1 ]] || usage
	receive "$1" status </dev/null
}

cmd_unhold() {
	[[ $# -eq 1 ]] || usage
	receive "$1" unhold </dev/null
}

cmd_rollback() {
	[[ $# -ge 1 ]] || usage
	local env=$1 target="" hold=""
	shift
	env_user "$env" >/dev/null
	while [[ $# -gt 0 ]]; do
		case $1 in
		--hold) hold=--hold ;;
		[0-9a-f]*) [[ $1 =~ ^[0-9a-f]{40}$ ]] || die "not a full commit sha: $1"; target=$1 ;;
		*) usage ;;
		esac
		shift
	done
	# shellcheck disable=SC2086 # $target and $hold are single words or empty
	receive "$env" rollback $target $hold </dev/null
	say "live version.txt: $(live_version "$env" || echo unreachable)"
}

cmd_deploy() {
	local env=$1 commit="" dir="" fast=0 live=1 any_commit=0
	shift
	env_user "$env" >/dev/null
	while [[ $# -gt 0 ]]; do
		case $1 in
		--commit) [[ $# -ge 2 ]] || usage; commit=$2; shift ;;
		--dir) [[ $# -ge 2 ]] || usage; dir=$2; shift ;;
		--fast) fast=1 ;;
		--no-live) live=0 ;;
		--any-commit) any_commit=1 ;;
		*) usage ;;
		esac
		shift
	done
	command -v uv >/dev/null || die "uv is not installed"

	commit=$(git -C "$repo" rev-parse --verify --quiet "${commit:-HEAD}^{commit}") ||
		die "unknown commit: ${commit:-HEAD}"
	if [[ $env == production && $any_commit == 0 ]] &&
		! git -C "$repo" merge-base --is-ancestor "$commit" origin/main; then
		die "$commit is not on origin/main (git fetch first, or pass --any-commit)"
	fi

	work=$(mktemp -d "${TMPDIR:-/tmp}/tff-deploy.XXXXXX")
	trap 'cleanup' EXIT
	local site=$work/site tools=$repo

	if [[ -n $dir ]]; then
		[[ -d $dir ]] || die "not a directory: $dir"
		say "copying $dir"
		mkdir "$site"
		cp -R -- "$dir"/. "$site"/
		if [[ ! -e $site/version.txt ]]; then
			printf 'commit=%s\n' "$commit" >"$site/version.txt"
		fi
		grep -qx "commit=$commit" "$site/version.txt" ||
			die "$dir/version.txt does not say commit=$commit"
	else
		say "building $commit in a temporary worktree"
		git -C "$repo" worktree add --quiet --detach "$work/src" "$commit"
		tools=$work/src
		(
			cd "$tools"
			# The catalog, chosen as deploy.yml chooses it: the committed real one, else (on
			# staging only, until Milestone 1's first real run) the synthetic sample.
			data=$tools/build/catalog-site.json
			if [[ ! -f $data ]]; then
				[[ $env == staging ]] || die "build/catalog-site.json is missing; production never gets the sample"
				data=$tools/tests/fixtures/catalog-site.sample.json
				say "no build/catalog-site.json at $commit; staging gets the synthetic sample"
			fi
			if [[ $env == production ]] && grep -q '"synthetic": *true' "$data"; then
				die "$data is synthetic; production never gets synthetic data"
			fi
			# Header phase A strips production's CSP while the stub has an inline style; the
			# list must never go live without it (/privacy says it is sent). The stub itself
			# moves with --dir, above.
			if [[ $env == production && -f ops/Caddyfile ]] &&
				grep -Eq '^[[:space:]]*header -Content-Security-Policy' ops/Caddyfile; then
				die "ops/Caddyfile is still in header phase A; do phase B first (ops/deploy/README.md)"
			fi
			uv sync --locked --group browser
			uv run tff-site fetch-fonts --data "$data"
			# The test site also publishes draft blog posts (Milestone 2 step 7b).
			drafts=()
			if [[ $env == staging ]]; then drafts=(--drafts); fi
			uv run tff-site build --out "$site" --commit "$commit" --data "$data" "${drafts[@]}"
			uv run tff-site check "$site"
			if [[ $fast == 0 ]]; then
				# The tests compare the page with the catalog it was built from (TFF_SITE_DATA);
				# the performance budget runs in CI, as deploy.yml leaves it out.
				TFF_SITE_DIR=$site TFF_SITE_DATA=$data uv run --group browser pytest tests/site -q \
					--ignore=tests/site/test_perf.py --browser chromium --browser firefox
			fi
		)
	fi

	say "packing"
	(cd "$tools" && uv run tff-site pack "$site" --out /dev/null --only /dev/null \
		--manifest "$work/site.manifest.json")
	local total need
	total=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["files"]))' \
		"$work/site.manifest.json")

	say "planning upload to $env"
	receive "$env" plan "$commit" <"$work/site.manifest.json" >"$work/need.txt"
	need=$(wc -l <"$work/need.txt")
	say "uploading $need of $total files"
	(cd "$tools" && uv run tff-site pack "$site" --only "$work/need.txt") |
		receive "$env" upload "$commit"

	say "switching $env to $commit"
	receive "$env" activate "$commit" </dev/null

	if [[ $live == 0 ]]; then
		say "done; live test skipped (--no-live)"
		return
	fi
	say "checking the live version.txt"
	local seen="" i
	for i in 1 2 3 4 5 6 7 8 9 10; do
		seen=$(live_version "$env" || true)
		[[ $seen == "$commit" ]] && break
		sleep 3
	done
	[[ $seen == "$commit" ]] ||
		die "live version.txt says '${seen:-nothing}', not $commit; roll back with: ops/deploy.sh rollback $env"
	if [[ -z $dir ]]; then
		say "running the live test"
		(cd "$tools" && uv run --group browser pytest tests/live -q \
			--base-url "$(site_url "$env")" --expect-commit "$commit" \
			--browser chromium --browser firefox) ||
			die "live test failed on $env; roll back with: ops/deploy.sh rollback $env"
	fi
	say "deployed $commit to $env"
}

cleanup() {
	if [[ -n ${work:-} && -d $work ]]; then
		if [[ -d $work/src ]]; then
			git -C "$repo" worktree remove --force "$work/src" 2>/dev/null ||
				git -C "$repo" worktree prune
		fi
		rm -rf -- "$work"
	fi
}

[[ $# -ge 1 ]] || usage
case $1 in
status) shift; cmd_status "$@" ;;
unhold) shift; cmd_unhold "$@" ;;
rollback) shift; cmd_rollback "$@" ;;
production | staging) cmd_deploy "$@" ;;
-h | --help) usage ;;
*) usage ;;
esac
