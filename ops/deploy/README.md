# Deploys

How a built site reaches `/srv/trulyfreefonts` on the server (Milestone 2 step 11; design in `design-m2.md` §5). GitHub Actions deploys each push to `main` (production) and `staging` (the test site) as a restricted user; `ops/deploy.sh` is the laptop fallback (M2-D6).

| File | What it is |
|---|---|
| [tff-receive](tff-receive) | The only program the deploy keys can run. Python 3.13, standard library only; installed as `/usr/local/sbin/tff-receive`. |
| [sshd-deploy.conf](sshd-deploy.conf) | `/etc/ssh/sshd_config.d/10-deploy.conf`: forced command, keys in root-owned files, no PTY or forwarding. |
| [setup-server.sh](setup-server.sh) | Creates the users, directories and key files (stage A) and changes sshd (stage B). Idempotent. |
| [deploy_key_tests.sh](deploy_key_tests.sh) | Proves a deploy key can do nothing but deploy. |
| [badtars.py](badtars.py) | The malicious uploads the key tests and `tests/ops/test_receive.py` send. |
| [../deploy.sh](../deploy.sh) | The laptop fallback: build, check, upload, switch, live test; rollback and status. |
| [../tests/deploy_loop.py](../tests/deploy_loop.py) | Fetches the page and its files every 100 ms during a deploy; must end `errors=0 mixed=0`. |

## On the server

```
/srv/trulyfreefonts/                     root:root 755 (after the migration)
  public -> prod/current                 root-owned symlink: the deploy user can't repoint it
  prod/                                  deploy:deploy 755
    releases/<sha40>/                    one complete site per commit
    releases/<sha40>.manifest.json       its manifest, outside the served tree
    current -> releases/<sha40>          switched with one rename
    incoming/                            700: plans and partial uploads
    history.log  .lock  .hold
  staging/                               the same, owned by deploy-staging; Caddy serves staging/current
```

Caddy's roots must be `public` (that is, `prod/current`) and `staging/current`, never `staging/` or `prod/` themselves: those would also serve `history.log` and the release manifests.

- Users `deploy` and `deploy-staging`: system users, shell `/bin/sh` (sshd runs the forced command through the login shell), locked password, home `/var/lib/tff-deploy[-staging]` (root-owned), no sudo, no other groups. Unix permissions keep staging's key out of `prod/`.
- Keys: `/etc/ssh/authorized_keys/deploy` and `.../deploy-staging`, `root:root 644`, one line each: `restrict,command="/usr/local/sbin/tff-receive production" ssh-ed25519 AAAA… tff-deploy-production-2026-10`.

## How a deploy works

A deploy is three calls to `tff-receive`, each its own SSH session:

1. `plan <sha>` with `site.manifest.json` on stdin (`tff-site pack --manifest`): prints the paths whose content is in no kept release.
2. `upload <sha>` with `tff-site pack --only need.txt` on stdin: checks every tar member (regular files and directories only; the path rule and extension list in site/CONTRACT.md; no setuid, setgid or sticky bit; 20 MB per file, 400 MB and 20,000 files in total; only paths in the plan, with matching size and sha256), hardlinks everything else from kept releases, checks the release is complete and that `version.txt` says `commit=<sha>`, then renames it into `releases/<sha>`.
3. `activate <sha>`: hardlinks the current release's `assets/` files that the new one lacks (so open tabs can still load their details and specimens), switches `current` with one `rename`, logs to `history.log`, keeps the newest 3 releases (never the current or previous one) and clears `incoming/` entries older than a day.

Disk use stays bounded even if a key is misused: at most 4 plans wait in `incoming/`, and at most 2 uploads that were never activated are kept, for a day. A release that was pruned can be uploaded again (to go back to an old commit): it then counts as a fresh upload, not by its old activation.

Other verbs: `rollback [sha] [--hold]` switches back to the previous release, or to a named kept one; `--hold` blocks `activate` until `unhold`. Two rollbacks in a row go back two releases. `status` shows the current release, the previous one, the hold and the kept releases.

Exit codes: 0 done, 2 usage (including any command not in the list above), 3 invalid input, 4 locked or held, 5 internal error. Every command, and every refused one, is logged to syslog as `tff-receive` (`journalctl -t tff-receive`).

## From the laptop

```sh
ops/deploy.sh staging                      # HEAD: build in a temporary worktree, test, upload, switch, live test
ops/deploy.sh production --commit <sha>    # production takes only commits on origin/main (--any-commit overrides)
ops/deploy.sh staging --fast               # skip the site tests before uploading (the live test still runs)
ops/deploy.sh rollback production          # one command; add --hold to stop the next deploy
ops/deploy.sh rollback production <sha>    # to a named kept release
ops/deploy.sh unhold production
ops/deploy.sh status production
```

It runs everything through Byron's login (`ssh tff sudo -n -u deploy /usr/local/sbin/tff-receive production …`), so no deploy key lives on the laptop.

Check a deploy is atomic: in a second terminal, while `ops/deploy.sh staging` runs,

```sh
uv run python ops/tests/deploy_loop.py https://staging.purelyfreefonts.com --interval 0.1 --duration 120
```

It must end with `errors=0 mixed=0`. Then `ops/deploy.sh rollback staging && curl -s https://staging.purelyfreefonts.com/version.txt` shows the previous commit.

## Setting up the server

Order (design-m2 §7): stage A is additive and safe; stage B changes sshd and is gated by the owner (batch 3, Q1–Q2).

**Stage A** (users, directories, tff-receive, key files; no sshd change):

```sh
scp -r ops/deploy tff:/tmp/tff-deploy
ssh tff sudo bash /tmp/tff-deploy/setup-server.sh stage-a \
  --key-production /tmp/tff-deploy/production.pub --key-staging /tmp/tff-deploy/staging.pub
ssh tff sudo -u deploy /usr/local/sbin/tff-receive production status
```

The keys can be added later by running stage A again with `--key-*`; without them the key files stay empty.

**Keys.** Generate each pair in the scratchpad, never in the repository:

```sh
ssh-keygen -t ed25519 -N '' -C tff-deploy-staging-2026-10 -f "$SCRATCH/staging"
```

After stage B, run the key tests right away, then store the private key as the environment secret and destroy the local copy:

```sh
ops/deploy/deploy_key_tests.sh "$SCRATCH/staging" deploy-staging
gh secret set DEPLOY_SSH_KEY --env staging < "$SCRATCH/staging"
shred -u "$SCRATCH/staging"
```

**Lockout precautions for stage B.**

1. Open a master connection that survives a bad sshd config, and send every fix through it:
   `ssh -o ControlMaster=yes -o ControlPath=$SCRATCH/tff.ctl -o ControlPersist=60m -fN tff`
2. `ssh -S $SCRATCH/tff.ctl tff sudo bash /tmp/tff-deploy/setup-server.sh stage-b --control-connection-open`
   It saves `00-hardening.conf.bak`, adds the deploy users to `AllowUsers`, installs `10-deploy.conf`, runs `sshd -t` and checks `sshd -T` (Byron still allowed with no forced command; the deploy users forced, no PTY, no forwarding). Only then does it `systemctl reload ssh` (a reload, not a restart); otherwise it puts the old files back.
3. Before closing the master: `ssh -o ControlPath=none tff true`.
4. To undo: `ssh -S $SCRATCH/tff.ctl tff sudo bash /tmp/tff-deploy/setup-server.sh stage-b-undo`.
5. Last resort: the VNC console (ops/SERVER.local.md). The home IP is ignored by fail2ban.

## GitHub settings (the owner's, before the first Actions deploy)

None of these exist yet (checked read-only on 2026-09-26). GitHub creates an unprotected environment the first time a job names one, so create each environment with its branch rule before adding its secrets.

1. **Environments.** `production`, deployments from `main` only; `staging`, deployments from `staging` only (Settings → Environments → Deployment branches and tags → Selected branches).
2. **Environment secrets** (each environment its own): `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` and `DEPLOY_HOST`; **variables** `DEPLOY_USER` (`deploy` or `deploy-staging`) and, optionally, `SITE_URL`, the address the live checks use (without it, `https://purelyfreefonts.com` for production and `https://staging.purelyfreefonts.com` for staging). `DEPLOY_HOST` is the origin's IP address, kept only as an environment secret (Actions masks it in logs). Never create a DNS name for it: an unproxied (grey-cloud) record would publish the origin IP, which lets anyone bypass Cloudflare (AUTHORITY.md, `ops/SERVER.local.md`).
3. **Labels** the issue forms and the deploy report apply; GitHub silently drops a form label that doesn't exist (the list is `REPO_LABELS` in `tests/test_issue_forms.py`). *(Done by Claude with `gh` on 2026-09-30.)*
   ```sh
   gh label create license --description "License report (issue form)"
   gh label create missing-font --description "Missing font (issue form)"
   gh label create usability --description "Hard to use or confusing (issue form)"
   gh label create bug --description "Something's broken (issue form)" --force
   gh label create deploy-failure --description "Opened by the deploy workflow"
   ```
4. **Refresh (Milestone 1 step 19).** `DATA_STORE_KEY` goes in an environment that only `main` may use (for example `refresh`), not in a repository secret, which every branch's workflows can read. Turn on "Allow GitHub Actions to create and approve pull requests" (Settings → Actions → General); the owner's ruling of 2026-09-26 (CI1) lets Claude do this with `gh` when step 19 is ready.
5. **Optional hardening.** Settings → Actions → General: "Require actions to be pinned to a full-length commit SHA" (every workflow already pins by SHA).
6. **Before `PRODUCTION_DEPLOYS=on`:** Caddy header phase B must be live (step 6 below). Until `ops/Caddyfile` drops `header -Content-Security-Policy`, both deploy.yml and `ops/deploy.sh` refuse to build a production deploy.

## Moving the live site into releases (one time)

*Done on 2026-09-30, with the stub at 0e3d4cb (`stub.css`, #37). Steps 1–5 below are as run; step 6, Caddy phase B, followed the same day (#39).*

The stub in `public/` was a real directory. After the users exist and the stub's `<style>` has moved to `stub.css`:

1. `ops/deploy.sh production --dir public --commit <sha of the externalized-stub PR> --no-live`
2. `ssh tff sudo ln -s prod/current /srv/trulyfreefonts/public.new`
3. `ssh tff sudo mv -T --exchange /srv/trulyfreefonts/public.new /srv/trulyfreefonts/public` (atomic; `public.new` then holds the old directory). The `-T` matters: without it `mv` takes the directory `public` as a destination folder and fails.
4. Check: `curl -s -H 'Accept: text/html' https://trulyfreefonts.com/ | grep 'Coming soon'` and `ssh tff readlink -f /srv/trulyfreefonts/public`
5. `ssh tff 'sudo mv -T /srv/trulyfreefonts/public.new /root/public-stub-<date> && sudo chown root:root /srv/trulyfreefonts'` (the old stub is kept aside in `/root`, not deleted)
6. Caddy phase B (design-m2 §4): delete the `header -Content-Security-Policy` line from the production block of `ops/Caddyfile`, merge, and run the deploy line at the top of the file. Check: `curl -sI https://trulyfreefonts.com/ | grep -i content-security-policy`. Production deploys of the list refuse to run until this is done.
7. Replace the rsync line in ops/SERVER.md and DONATIONS.md step 12: after the migration, `rsync … public/` would write straight into a live release. *(Done 2026-09-30.)*

## Tests

- `uv run pytest tests/ops/test_receive.py`: the receiver against temporary directories (full cycle, every malicious tar, the command grammar, locking, disk bounds, crash leftovers), the deploy loop watching real switches, the sshd drop-in parsed by the local sshd, `ops/deploy.sh` through a fake `ssh` (`--dir`, and the build path in a throwaway repository), and `deploy_key_tests.sh` against an sshd run as the current user with the drop-in's settings (marked `slow`; skipped without sshd). Set `TFF_RECEIVE_PYTHON=/path/to/python3.13` to also run the cycle under the server's Python.
- `uv run pytest tests/site/test_pack.py`: `tff-site pack` and `fetch-fonts`.
- `ops/deploy/deploy_key_tests.sh KEY USER`: against the real server, after stage B. `TFF_KEYTEST_SSH_OPTS` adds client options to every call (for example `-o UserKnownHostsFile=FILE` in Actions).
