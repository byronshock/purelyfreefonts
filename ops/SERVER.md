# Server: trulyfreefonts

How the Contabo VPS and the three Cloudflare zones are set up. The settled decisions are in [AUTHORITY.md](../AUTHORITY.md#infrastructure); this file is the checklist and runbook.

## Facts

| | |
|---|---|
| Host | Contabo Cloud VPS 4 (2026), Seattle, Debian 13 (Trixie) |
| IPv4 / IPv6 | in `ops/SERVER.local.md` (not in git) |
| SSH | `ssh tff` (user `byron`, key `~/.ssh/id_ed25519`, passwordless sudo) |
| Site root | `/srv/trulyfreefonts/public` |
| Web server config | [ops/Caddyfile](Caddyfile) → `/etc/caddy/Caddyfile`, which imports [ops/caddy/site.caddy](caddy/site.caddy) → `/etc/caddy/site.caddy` (the site's headers) and other sites' snippets from `/etc/caddy/sites/*.caddy` |
| Access log | `/var/log/caddy/access.log`: IPs masked to /16 (IPv4) and /32 (IPv6), IP headers and port dropped; from item 25, also `Referer` and `User-Agent` (owner ruling of 2026-09-26), `Cookie` and the location headers finer than the country; 14 days kept by logrotate ([ops/logrotate-caddy](logrotate-caddy) → `/etc/logrotate.d/caddy-trulyfreefonts`) |
| Origin cert | `/etc/caddy/certs/` (Cloudflare Origin CA, 15 years) |
| Cloudflare zone IDs | in `ops/SERVER.local.md`, or `ops/cf.sh GET /zones` |
| Cloudflare token | `~/.config/trulyfreefonts/cloudflare.env` on the laptop (mode 600, never committed) |
| Emergency access | Contabo panel → VNC console (address in `ops/SERVER.local.md`), root password in password manager |
| Snapshots | 1 slot on this plan (2 on the next tier up), each deleted after 30 days. Use one as an undo point before risky changes, not as a backup. To rebuild, use `ops/` + `public/` + this checklist. |

**Deploy the site** (from the project root): `rsync -av --delete public/ tff:/srv/trulyfreefonts/public/`

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
- [ ] 25. Deploy the access-log trim of 2026-09-26 with the deploy line at the top of [ops/Caddyfile](Caddyfile), then run the log check under Verification. It drops `Referer` and `User-Agent` (owner ruling of 2026-09-26, `log_fields`) and `Cookie` and Cloudflare's location headers finer than the country (owner ruling of 2026-09-28, `log_extra_headers`) (`Cf-Ipcity`, `Cf-Region`, `Cf-Postal-Code` and the like), plus the rarer IP headers (`Cf-Connecting-Ipv6`, `Cf-Pseudo-Ipv4`, `True-Client-Ip`, `X-Real-Ip`). */privacy/* already describes the trimmed log. *(Not yet deployed: on 2026-09-26 the live `/etc/caddy/Caddyfile` still had the filter of item 18. It ships with the Milestone 2 Caddyfile, which also adds `site.caddy`.)*
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
