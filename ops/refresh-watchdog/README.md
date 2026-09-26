# Refresh watchdog

GitHub turns off a public repository's scheduled workflows after 60 days without activity, and sends no warning. The monthly refresh (`.github/workflows/refresh.yml`) would then stop without anyone noticing. This watchdog runs on the VPS, outside GitHub's scheduler, once a day (Milestone 1, step 19).

It reads GitHub's public API and warns when:

- no refresh pull request has appeared for 35 days (a new pull request, or a new commit on the branch `refresh/monthly`, which is how a run replaces an unmerged pull request);
- `main` has had no commit for 50 days;
- the workflow `refresh.yml` is not active, for example `disabled_inactivity`.

Warnings go to the journal. If a token is installed, the watchdog also opens one GitHub issue, "Refresh watchdog: the monthly refresh needs attention", and opens no second one while that one is open (gate CI2, recommended option).

## Files

| File | Installed as |
|---|---|
| [tff-refresh-watchdog](tff-refresh-watchdog) | `/usr/local/sbin/tff-refresh-watchdog` (root:root 0755). Python 3.13, standard library only. |
| [tff-refresh-watchdog.service](tff-refresh-watchdog.service) | `/etc/systemd/system/`. A one-shot job under a throwaway system user. |
| [tff-refresh-watchdog.timer](tff-refresh-watchdog.timer) | `/etc/systemd/system/`. Daily, within an hour of midnight UTC; catches up after downtime. |

## Install

Install it once the first refresh pull request exists; before that, it warns every day. From the project root:

```sh
scp ops/refresh-watchdog/tff-refresh-watchdog ops/refresh-watchdog/tff-refresh-watchdog.{service,timer} tff:/tmp/
ssh tff 'sudo install -o root -g root -m 755 /tmp/tff-refresh-watchdog /usr/local/sbin/ &&
  sudo install -o root -g root -m 644 /tmp/tff-refresh-watchdog.service /tmp/tff-refresh-watchdog.timer /etc/systemd/system/ &&
  sudo systemctl daemon-reload && sudo systemctl enable --now tff-refresh-watchdog.timer &&
  rm /tmp/tff-refresh-watchdog /tmp/tff-refresh-watchdog.service /tmp/tff-refresh-watchdog.timer'
```

## The token (for issues)

Without a token the watchdog only writes to the journal. To have it open issues:

1. Byron creates a fine-grained personal access token on GitHub: resource owner `byronshock`, repository access "Only select repositories" with `byronshock/trulyfreefonts`, and one permission, Issues: Read and write. Give it an expiry date and put a reminder in the calendar to renew it.
2. Install it on the server without it touching the shell history:
   ```sh
   ssh tff 'sudo install -d -m 700 /etc/tff-refresh-watchdog &&
     sudo sh -c "umask 077; cat > /etc/tff-refresh-watchdog/github-token"'
   ```
   Paste the token, press Enter, then Ctrl-D.

systemd hands the file to the service as the credential `github-token`; the service never sees it in its environment. When the file is missing, the unit falls back to the placeholder `none`, which means no token. An expired token shows up in the journal as `could not open the issue: … HTTP 401`, and the unit fails.

## Check it

```sh
ssh tff 'sudo systemctl start tff-refresh-watchdog.service; journalctl -u tff-refresh-watchdog -n 20 --no-pager'
ssh tff 'systemctl list-timers tff-refresh-watchdog.timer'
```

A healthy run logs one line starting `tff-refresh-watchdog: ok:`. A warning makes the unit fail, so it also shows in `systemctl --failed`.

To try it by hand without opening an issue, from the laptop or the server: `ops/refresh-watchdog/tff-refresh-watchdog --dry-run`, optionally with `--now 2026-12-31T00:00:00Z` to see what it would say on a later day.

Exit codes: 0 all fine; 1 a warning; 2 a usage error; 3 GitHub could not be read or the issue could not be opened.

## When it warns

- **Workflow disabled:** `gh workflow enable refresh.yml -R byronshock/trulyfreefonts`.
- **No refresh pull request:** run it now with `gh workflow run refresh.yml -R byronshock/trulyfreefonts`, then look at the run. A failed run opens its own issue, "Monthly refresh failed".
- **No commit on main for 50 days:** merge the open refresh pull request, or push any other commit to `main`. A commit on `main` keeps the schedule alive.

Close the watchdog issue once a refresh pull request has appeared. The watchdog opens a new one if the problem comes back.
