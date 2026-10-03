# Server: trulyfreefonts

How the Contabo VPS and the Cloudflare zones are set up: three from 2026-09-25, and four more from 2026-10-02 (section K). The settled decisions are in [AUTHORITY.md](../AUTHORITY.md#infrastructure); this file is the checklist and runbook.

## Facts

| | |
|---|---|
| Host | Contabo Cloud VPS 4 (2026), Seattle, Debian 13 (Trixie) |
| IPv4 / IPv6 | in `ops/SERVER.local.md` (not in git) |
| SSH | `ssh tff` (user `byron`, key `~/.ssh/id_ed25519`, passwordless sudo) |
| Site root | `/srv/trulyfreefonts/public`, a root-owned symlink to `prod/current`, which points to one release in `prod/releases/<commit>/` (since 2026-09-30; layout in [ops/deploy/README.md](deploy/README.md)) |
| Test site | `staging.trulyfreefonts.com` (proxied DNS; `X-Robots-Tag: noindex, nofollow`), served from `/srv/trulyfreefonts/staging/current` |
| Deploy users | `deploy` and `deploy-staging` (stage A of [ops/deploy/README.md](deploy/README.md), 2026-09-30); they run only `/usr/local/sbin/tff-receive` |
| Web server config | [ops/Caddyfile](Caddyfile) → `/etc/caddy/Caddyfile`, which imports [ops/caddy/site.caddy](caddy/site.caddy) → `/etc/caddy/site.caddy` (the site's headers) and other sites' snippets from `/etc/caddy/sites/*.caddy` |
| Access log | `/var/log/caddy/access.log`: IPs masked to /16 (IPv4) and /32 (IPv6), IP headers and port dropped; from item 25, also `Referer` and `User-Agent` (owner ruling of 2026-09-26), `Cookie` and the location headers finer than the country; 14 days kept by logrotate ([ops/logrotate-caddy](logrotate-caddy) → `/etc/logrotate.d/caddy-trulyfreefonts`) |
| Origin cert | `/etc/caddy/certs/` (Cloudflare Origin CA, 15 years) |
| Cloudflare zone IDs | in `ops/SERVER.local.md`, or `ops/cf.sh GET /zones` |
| Cloudflare token | `~/.config/trulyfreefonts/cloudflare.env` on the laptop (mode 600, never committed) |
| Emergency access | Contabo panel → VNC console (address in `ops/SERVER.local.md`), root password in password manager |
| Refresh access | the monthly refresh (`.github/workflows/refresh.yml`) reaches the private data store `byronshock/trulyfreefonts-data` with a deploy key; its private half is only the `DATA_STORE_KEY` secret of `byronshock/trulyfreefonts` (section J) |
| Snapshots | 1 slot on this plan (2 on the next tier up), each deleted after 30 days. Use one as an undo point before risky changes, not as a backup. To rebuild, use `ops/` + `public/` + this checklist. |

**Deploy the site** (from the project root; never rsync into `public/`, which is now a live release):
- the test site: `ops/deploy.sh staging` (builds HEAD in a temporary worktree, runs the site tests, uploads, switches, then the live test);
- production: `ops/deploy.sh production --commit <sha on origin/main>` (it refuses until Caddy header phase B);
- **rollback**, one command: `ops/deploy.sh rollback production` (or `staging`; add `--hold` to stop the next deploy, then `ops/deploy.sh unhold production`);
- what is live: `ops/deploy.sh status production`, or `curl -s https://trulyfreefonts.com/version.txt`.

Details, the release layout and the Actions deploys are in [ops/deploy/README.md](deploy/README.md).

**Change the web server config:** edit [ops/Caddyfile](Caddyfile) or [ops/caddy/site.caddy](caddy/site.caddy), then run the deploy line at the top of the Caddyfile: it validates both and installs both.

**Cloudflare API:** `ops/cf.sh METHOD /path [json]`, e.g. `ops/cf.sh GET /zones`. It reads the token from the env file and never prints it.

**Cloudflare IP ranges:** `ssh tff sudo cloudflare-ips-sync` refreshes the firewall and Caddy by hand; a weekly timer does it automatically.

## Setup checklist

### A. By hand (Byron)
- [x] 1. Get the IPv4 (and IPv6) from Contabo. Change the emailed root password and store it in the password manager.
- [x] 2. `ssh-copy-id -i ~/.ssh/id_ed25519.pub root@<IPv4>` from your own terminal.
- [x] 3. Create the Cloudflare API token "trulyfreefonts-mgmt" (Zone·Zone·Read, Zone·DNS·Edit, Zone·Zone Settings·Edit, Zone·SSL and Certificates·Edit; the 3 zones). Put `CF_API_TOKEN=<token>` in `~/.config/trulyfreefonts/cloudflare.env`.

### B. Server baseline (Claude, over SSH)
- [x] 4. `Host tff` in `~/.ssh/config`; allow rules for ssh/scp/rsync to `tff` in `.claude/settings.local.json`.
- [x] 5. `apt full-upgrade`; hostname `trulyfreefonts`; timezone UTC; install `sudo ufw fail2ban unattended-upgrades`; automatic security updates on.
- [x] 6. User `byron` with the key and NOPASSWD sudo (`/etc/sudoers.d/byron`).
- [x] 6b. Remove cloud-init's default `debian` user (locked, but has NOPASSWD sudo) and `/etc/sudoers.d/90-cloud-init-users`.
- [x] 7. `/etc/ssh/sshd_config.d/00-hardening.conf`: no root login, no password or keyboard-interactive auth, `AllowUsers byron`.
- [x] 8. `ufw`: deny incoming; allow 22, 80, 443. (80/443 later limited to Cloudflare in step 14.) fail2ban sshd jail (`backend = systemd`, home IP ignored). LLMNR/mDNS off in systemd-resolved.
- [x] 9. Reboot and confirm it comes back.

### C. Cloudflare (Claude, over the API)
- [x] 10. Proxied `A`/`AAAA` records for `@` and `www` in all 3 zones.
- [x] 11. SSL Full (strict), Always Use HTTPS, minimum TLS 1.2 on all 3 zones.
- [x] 12. Origin CA cert for `trulyfreefonts.{com,org,net}` and `*.trulyfreefonts.{com,org,net}`, key generated on the server.

### D. Web server (Claude)
- [x] 13. Caddy from the official repo; `.com` serves the site; `www.*`, `.org` and `.net` 301 to `https://trulyfreefonts.com{uri}`; placeholder page.

### E. Optional
- [x] 14. Ports 80/443 open only to Cloudflare's IP ranges; Caddy trusts `CF-Connecting-IP` from them. Kept current weekly by `cloudflare-ips-sync.timer` ([ops/cloudflare-ips-sync](cloudflare-ips-sync)).
- [x] 15. Cloudflare Email Routing `admin@trulyfreefonts.com` → Gmail; "no mail" SPF/DMARC on .org and .net. (Email Routing records: MX route1–3.mx.cloudflare.net, SPF, DKIM `cf2024-1`.)
- [x] 16. Contabo snapshot (taken 2026-09-25; auto-deleted after 30 days, around 2026-10-25).

### F. Visitor privacy (Claude)
- [x] 17. Cloudflare Network Error Logging (the `NEL` / `Report-To` headers, which made browsers report connection failures to a.nel.cloudflare.com) turned off on all 3 zones (`PATCH /zones/<id>/settings/nel` `{"value":{"enabled":false}}`). *(2026-09-25)*
- [x] 18. Access log privacy: the Caddyfile log filter masks `remote_ip` and `client_ip` (/16, /32) and drops `remote_port`, `Cf-Connecting-Ip` and `X-Forwarded-For`; Caddy's rolling is off; `logrotate` installed and keeps 14 days, rotated daily. Lines logged before the change were masked in place. *(2026-09-25)*
- [x] 25. Deploy the access-log trim of 2026-09-26 with the deploy line at the top of [ops/Caddyfile](Caddyfile), then run the log check under Verification. It drops `Referer` and `User-Agent` (owner ruling of 2026-09-26, `log_fields`) and `Cookie` and Cloudflare's location headers finer than the country (owner ruling of 2026-09-28, `log_extra_headers`) (`Cf-Ipcity`, `Cf-Region`, `Cf-Postal-Code` and the like), plus the rarer IP headers (`Cf-Connecting-Ipv6`, `Cf-Pseudo-Ipv4`, `True-Client-Ip`, `X-Real-Ip`). */privacy/* already describes the trimmed log. *(Deployed 2026-09-30 with the Milestone 2 Caddyfile and `site.caddy`, header phase A; the old file is `/etc/caddy/Caddyfile.bak-2026-09-30`. The log check passed: masked IPs, no port, and of the request headers only `Accept`, `Accept-Encoding`, `Cdn-Loop`, `Cf-Ipcountry`, `Cf-Ray`, `Cf-Visitor` and `X-Forwarded-Proto`.)*
- [x] 19. On all 3 zones: Email Address Obfuscation off (`PATCH /zones/<id>/settings/email_obfuscation` `{"value":"off"}`), since it injects a script; Rocket Loader and Always Online confirmed off; Browser Cache TTL set to "Respect Existing Headers" (`browser_cache_ttl` `{"value":0}`, was 14400). *(2026-09-25; Milestone 2 step 9)*

### G. Visitor privacy, dashboard only (Byron, by hand)
The API token can't reach these two settings: its calls to Bot Management and Web Analytics return "Authentication error". Sign in at `dash.cloudflare.com`, do both, then tell Claude, who runs the Verification checks below.
- [x] 20. **Web Analytics automatic setup: Disable.** It was live: on 2026-09-25, every browser request from outside Europe got Cloudflare's beacon, `<script src="https://static.cloudflareinsights.com/beacon.min.js/…">`, injected before `</body>`. *(Done 2026-09-25: Byron disabled RUM on the `.com`, `.org` and `.net` cards and confirmed it under Speed → Real user monitoring. Claude verified it from PDX (US): 5 of 5 `Accept: text/html` requests had no beacon, no script, no `/cdn-cgi/` path and no cookie.)*
  1. From Account home, go to **Analytics & logs → Web analytics**.
  2. On the `trulyfreefonts.com` card, choose **Manage site**.
  3. Under **Real User Measurements (RUM)**, choose **Disable**, then **Update**. Don't choose "Enable with JS Snippet installation", which leaves the site on, waiting for a snippet you add yourself. Don't choose **Delete** under Advanced Options either: Cloudflare promises not to turn a *disabled* site back on, and a deleted one leaves no record that you opted out.
  4. Do the same for any other card for these domains (`trulyfreefonts.org`, `trulyfreefonts.net`, or a `www.` host). One card covers the apex and `www`. Don't use **Add a site** for domains that aren't listed, because adding a proxied site switches the beacon on.
  5. If `trulyfreefonts.com` has no card, open the `trulyfreefonts.com` zone and go to **Analytics & logs → Web analytics**. If it offers **Manage RUM Settings**, choose **Disable**, then **Update**. If it offers only **Enable Globally** and **Exclude EU**, this is Cloudflare's documented opt-out: click **Exclude EU**, then **Manage RUM Settings → Disable → Update**.
  6. In the `trulyfreefonts.com` zone, also open **Speed → Real user monitoring**. If it shows RUM as active, or offers a way to disable it, disable it. Never click **Enable RUM** on the Speed pages later; it turns the same beacon back on. (The zone's `rum` setting in the API already reads `off` while the beacon is injected, so that setting isn't the switch.)
  7. Tell Claude which cards you found and what you left each one as.
- [x] 21. **Bot Fight Mode: Off, on each of `.com`, `.org` and `.net`.** *(Done 2026-09-25: Byron found Bot Fight Mode and AI Labyrinth already off on all three. Checked from outside: the page carries no injected script, so Precursor isn't on, and `robots.txt` has no Cloudflare-managed block. The AI bot policy values weren't recorded; the `robots.txt` item in Milestone 2 step 1 now checks the live file against the repo copy. On 2026-09-26 the live `robots.txt` did carry Cloudflare text, from a different setting: see step 24.)*
  1. Open the zone, go to **Security → Settings** (in the old dashboard, Security → Bots), and filter by **Bot traffic**.
  2. Set **Bot fight mode** to Off. On the Free plan, JavaScript detections has no toggle of its own and stops along with Bot Fight Mode.
  3. On the same list, check that **AI Labyrinth** (it adds hidden links to pages) and **Set your preference to block training in robots.txt** are Off; both are off by default. If there is a **Precursor** card (it injects a script, and may not exist on Free), check that it is Off too; clear the filter to see it. Leave the AI bot policies (Search, Agent, Training) as they are, but tell Claude what they say and whether a robots.txt sync option is on, since that would add lines to the site's own `robots.txt` in Milestone 2.
  4. Switch to `trulyfreefonts.org` and repeat steps 1–3, then do the same for `trulyfreefonts.net`.

### H. Cache Rule for hashed assets (Byron, then Claude)
Hashed files under `/assets/` never change, so Cloudflare may cache them for a year. Cloudflare doesn't cache JSON or HTML by default, and the API token can't create Cache Rules yet. HTML stays uncached (M2-D11 (a)).
- [x] 22. **Byron: add the Cache Rules permission to the token.** *(Done 2026-09-25.)* Cloudflare dashboard → profile icon → **My Profile** → **API Tokens** → **trulyfreefonts-mgmt** → **⋯** → **Edit** → **Permissions** → **+ Add more**: **Zone** · **Cache Rules** · **Edit**. Leave Zone Resources as they are, then **Continue to summary** → **Update token**. The token value stays the same. Tell Claude when it's done.
- [x] 23. **Claude: create the rule on `trulyfreefonts.com`** through the API (phase `http_request_cache_settings`): "URI path starts with `/assets/`": eligible for cache, edge TTL from the origin's Cache-Control. Record the ruleset id here. (Milestone 2 step 11.) *(Done 2026-09-25: ruleset `86856f17b4ce4ce39bddf524e52e88d1`, rule `0463441450b642bf991f080a7aa91d4e`, expression `starts_with(http.request.uri.path, "/assets/")`, cache on, edge and browser TTL `respect_origin`. HTML stays uncached.)*

### I. Cloudflare's text in robots.txt (Claude)
On the Free plan, a zone whose origin has no `robots.txt`, and whose managed robots.txt is off, gets Cloudflare's **Content Signals Policy** served as its `robots.txt`: about 25 lines of legal comments. On 2026-09-26 `https://trulyfreefonts.com/robots.txt` served it, because the stub site had no `robots.txt`. Byron found Bot Preference Sync (managed robots.txt's current name) off on `.com`. Cloudflare's documented opt-out, **Display Content Signals Policy** in the zone Overview's **Control AI Crawlers** card, wasn't in the dashboard.
- [x] 24. **Serve our own `robots.txt`, so Cloudflare adds nothing.** *(Done 2026-09-26: deployed from `main` at the merge of #16. `https://trulyfreefonts.com/robots.txt` matches `public/robots.txt`, with no Cloudflare text; `www.`, `.org` and `.net` 301 to it.)* `public/robots.txt` allows everything, the same as having no file (M2-D8 (a)). Deploy it, then check that the live file matches the repo's. The site's own `robots.txt` (Milestone 2 step 1) replaces it when the stub goes; a site without one would bring the Cloudflare text back.

### J. Monthly refresh access (Byron, by hand)
Milestone 1 step 19. The refresh workflow opens its own pull request and reaches the private data store with a deploy key. Byron runs these, since they change security settings and create a key; Claude checks the result with read-only `gh` calls.
- [x] 26. **GitHub Actions may open pull requests.** Settings → Actions → General → Workflow permissions: "Allow GitHub Actions to create and approve pull requests" on, and the default permissions left at read-only. *(Done 2026-09-30 with `gh api -X PUT repos/byronshock/trulyfreefonts/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true`; the API reports `can_approve_pull_request_reviews: true`.)*
- [x] 27. **The data store's deploy key and the `DATA_STORE_KEY` secret.** An ed25519 key made for this alone: its public half is the deploy key "trulyfreefonts refresh (DATA_STORE_KEY)" on `byronshock/trulyfreefonts-data`, **read-write**, because the refresh's `store` job pushes the month's new snapshots; its private half is only the `DATA_STORE_KEY` secret of `byronshock/trulyfreefonts`, which the `refresh` job uses to clone the store (not kept) and the `store` job to push. Fingerprint `SHA256:iHpu23uBZ4qzimjNuXZAQYNMA2+N9cHgbKFjkKT/YM4`. The private key was made in a temporary folder that the command deleted; it exists nowhere else. *(Done 2026-09-30: `gh repo deploy-key list --repo byronshock/trulyfreefonts-data` shows the key, read-write, and its fingerprint matches; `gh secret list` shows `DATA_STORE_KEY`.)*

  **To rotate it** (or if it may have leaked): run the same command again, which makes a new key, adds it and replaces the secret, then delete the old deploy key: `gh repo deploy-key list --repo byronshock/trulyfreefonts-data` gives its id, and `gh repo deploy-key delete <id> --repo byronshock/trulyfreefonts-data` removes it. Record the new fingerprint here.

  ```bash
  bash -c 'umask 077; d=$(mktemp -d); trap "rm -rf \"$d\"" EXIT; ssh-keygen -q -t ed25519 -N "" -C "trulyfreefonts refresh" -f "$d/key" && gh repo deploy-key add "$d/key.pub" --repo byronshock/trulyfreefonts-data --title "trulyfreefonts refresh (DATA_STORE_KEY)" --allow-write && gh secret set DATA_STORE_KEY --repo byronshock/trulyfreefonts < "$d/key" && ssh-keygen -l -f "$d/key.pub"'
  ```

### K. The new domains (2026-10-02)
The owner's rulings of 2026-10-02 (AUTHORITY.md, **Name and domain**): the site becomes Purely Free Fonts at purelyfreefonts.com, and four more zones join the three above. Zone ids stay in `ops/SERVER.local.md`, never here. Each zone gets the settings of items 11, 17 and 19:
- NEL, email obfuscation, Rocket Loader, Always Online, Speed Brain and Cloudflare Fonts off;
- browser cache TTL 0;
- SSL mode custom and strict;
- Always Use HTTPS, TLS 1.3 and automatic HTTPS rewrites on;
- minimum TLS 1.2.

Each also gets items 20 and 21's dashboard checks.
- [ ] 28. **truelyfreefonts.com**, a misspelling a staging tester typed. Cloudflare redirects it at the edge, with no origin.
  - [x] Byron: registered it, and added it to the token's zone resources, with **Zone · Single Redirect · Edit** for the redirect rules.
  - [x] Claude: applied the settings above and read them back.
  - [x] Claude: set up the edge redirect:
    - A Single Redirect (ruleset `db4d96dcad9c4633a56f41e01f714d1d`, rule `976d9c50bd394ef1aaebe44923993ad4`). It matches `http.host in {"truelyfreefonts.com" "www.truelyfreefonts.com"}`. It sends a 301 to `concat("https://trulyfreefonts.com", http.request.uri.path)`, keeping the query string.
    - Proxied placeholders for `@` and `www`: A `192.0.2.1` and AAAA `100::`, which route nowhere.
  - [x] Byron: turned on Email Routing for admin@ → Gmail, with no catch-all; a test message arrived. Claude: added DMARC `p=reject`.
  - [x] Byron, in the dashboard:
    - Web Analytics RUM is disabled.
    - Bot Fight Mode, AI Labyrinth and Bot Preference Sync are off.
    - The AI bot policies (Search, Agent, Training) are left at Allow.
  - [x] Claude: checked from the US, resolving through 1.1.1.1, on 2026-10-02. Twenty requests over http and https, with and without `www`, each gave one 301 to the same path and query. None carried `set-cookie`, `nel`, `report-to` or a CSP report header, and no body carried a script. `/robots.txt` 301s too.
  - [ ] At cutover: change the target to `https://purelyfreefonts.com`.
- [ ] 29. **purelyfreefonts.com**, the new canonical domain.
  - [x] Byron: registered it, added it to the token, turned on Email Routing for admin@ → Gmail (a test message arrived), and turned the bot settings off.
  - [x] Claude: applied the settings above and read them back. The universal edge certificate is active.
  - [x] Claude: added the `/assets/` cache rule as item 23 (ruleset `076d416701d74949b8b3b28f29fc7c37`, rule `d41e3b772ff941068d98620a32db3e88`) and DMARC `p=reject`.
  - [x] Claude: installed an Origin CA certificate for `purelyfreefonts.com` and `*.purelyfreefonts.com`:
    - ECC, valid to 2041-09-29;
    - its key was generated on the server and never left it;
    - the files are `/etc/caddy/certs/purely.pem` and `purely.key` (root:caddy, key mode 640);
    - the key matches the certificate, and Caddy can read it.

    The old certificate stays, for the redirects.
  - [x] Claude: proxied A and AAAA records for `@`, `www` and `staging`, pointing at the origin (never DNS-only). *(2026-10-02: copied from trulyfreefonts.com's own records; all three answer through Cloudflare.)*
  - [x] Claude: the Caddy blocks for `purelyfreefonts.com` and `staging.purelyfreefonts.com` (snippet `origin_tls_purely`), installed with the Caddyfile's deploy line. *(2026-10-02, from #53's branch, after CI's caddy job passed. The old hosts behave as before.)*
  - [x] Byron: Web Analytics RUM disabled, once the zone is proxied. *(2026-10-02)*
- [ ] 30. **purelyfreefonts.org and purelyfreefonts.net**, redirected at the edge like item 28.
  - [x] Byron: registered both and added them to the token.
  - [x] Claude: applied the settings above and read them back.
  - [x] Claude: added no-mail records to each: a null MX `0 .`, `v=spf1 -all`, and DMARC `p=reject`.
  - [x] Claude: added an edge 301 to `concat("https://purelyfreefonts.com", http.request.uri.path)`, keeping the query string. The .org ruleset is `056a456d0bf84307914493feb3b1048e` (rule `f8ff396e1e684e16ae1591a50778e09b`), and the .net ruleset is `493bc727bf354fdea2f2ea6d24d4bf9c` (rule `c027aacbeeee4c568e061eda8a4cd071`). Both stay inactive until the placeholders exist.
  - [x] Byron: on both zones, the bot settings and Web Analytics checks of item 28. *(2026-10-02: Web Analytics RUM disabled; Bot Fight Mode, AI Labyrinth and Bot Preference Sync off.)*
  - [ ] Claude, at cutover: the proxied placeholders for `@` and `www`, then the outside check.
- [ ] 31. **Cutover** (`docs/milestone-2.md` step 13b): Caddy serves purelyfreefonts.com, and `www.purelyfreefonts.com`, trulyfreefonts.com, .org, .net and their `www.` hosts 301 to `https://purelyfreefonts.com{uri}`. `staging.trulyfreefonts.com` 301s to `https://staging.purelyfreefonts.com{uri}`.

## Verification
- `ssh tff sudo -n true` works; `ssh root@<IP>` and `ssh -o PubkeyAuthentication=no tff` are refused.
- `ssh tff 'sudo ufw status verbose; systemctl is-active caddy fail2ban unattended-upgrades'` is all active.
- `dig +short trulyfreefonts.com` returns Cloudflare IPs.
- `curl -sI https://trulyfreefonts.com` → 200, `server: cloudflare`. `www.`, `.org` and `.net` URLs → 301 to the same path on `https://trulyfreefonts.com`.
- SSL mode is `strict` on all 3 zones.
- `curl -s https://trulyfreefonts.com/robots.txt | diff - public/robots.txt` prints nothing: no Cloudflare text.
- `curl -sI https://trulyfreefonts.com` has no `nel` or `report-to` header.
- `ops/cf.sh GET /zones/<id>/rulesets/phases/http_request_cache_settings/entrypoint` on the `.com` zone shows the one `/assets/` rule, enabled. `curl -sI https://trulyfreefonts.com/` shows `cf-cache-status: DYNAMIC` (HTML is never edge-cached); once the site is live, a second request for a hashed `/assets/` file shows `cf-cache-status: HIT`.
- On each zone, `ops/cf.sh GET /zones/<id>/settings/<name>` gives `email_obfuscation` off, `rocket_loader` off, `always_online` off and `browser_cache_ttl` 0.
- Injection checks must send a browser's `Accept: text/html` header. Plain `curl` sends `Accept: */*`, and Cloudflare injects nothing into that response, so it misses the beacon. `curl -s -H 'Accept: text/html' https://trulyfreefonts.com/ | grep -c -E 'cloudflareinsights|data-cf-beacon|/cdn-cgi/'` gives 0, and the same request with `-D - -o /dev/null` shows no `set-cookie`. First confirm that `curl -s https://trulyfreefonts.com/cdn-cgi/trace` shows `loc=US`: the default Web Analytics setting skips visitors in the EU, EEA, UK and Switzerland, so a clean result from there proves nothing.
- `ssh tff 'sudo tail -1 /var/log/caddy/access.log'` shows a masked `client_ip` (ending `.0.0` or `::`), no `remote_port`, and no `Cf-Connecting-Ip` or `X-Forwarded-For` header; once item 25 is deployed, also no `Referer`, `User-Agent` or `Cookie` header, and no Cloudflare location header but `Cf-Ipcountry`. `sudo logrotate --debug /etc/logrotate.d/caddy-trulyfreefonts` reports no errors.
- **truelyfreefonts.com** (item 28): resolve through 1.1.1.1 and pin curl to it, since a local resolver may still cache the name as missing. Then `curl -s -D - -o /dev/null -H 'Accept: text/html' 'https://truelyfreefonts.com/a/b?c=1'` gives `301` and `location: https://trulyfreefonts.com/a/b?c=1` (`https://purelyfreefonts.com/…` after cutover), with no `set-cookie`, `nel` or `report-to`. Check the same for `www.` and over `http://`.
