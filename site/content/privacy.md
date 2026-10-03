---
title: Privacy
description: >-
  No cookies, no analytics, nothing stored in your browser and nothing loaded from other
  sites. How to check, and what the server and Cloudflare log.
---

Purely Free Fonts doesn't track you. The pages set no cookies, run no analytics, store nothing in your browser and load nothing from any other site.

## What the pages do and don't do

- **No cookies.** Neither we nor Cloudflare, which delivers the site, set one.
- **No analytics or tracking.** No script, ours or anyone else's, counts or follows visitors.
- **Nothing stored in your browser.** The site uses no local storage, session storage, IndexedDB, cache storage or service worker. Your filters live in the page address, after the `#`, and browsers never send that part to any server.
- **Nothing loaded from other sites.** Every file the pages use comes from purelyfreefonts.com, including the site's own font, the font previews and the fonts for "Type your own text". Links to other sites, such as download pages, GitHub or the tip page, load nothing until you click them.
- **Enforced by your browser.** Our server sends a Content Security Policy that tells your browser to block any script, style, image, font or connection from another site, even if something tried to add one.

## Check for yourself

Open your browser's developer tools on any page here, choose the **Network** tab and reload the page. Every request listed should go to purelyfreefonts.com. (Your browser extensions may add requests of their own; they don't come from this site.) Then try the filters, open a font's details and scroll through the list: still only purelyfreefonts.com.

- **Firefox:** press Ctrl+Shift+E (Cmd+Option+E on a Mac). Or open the menu, choose More tools, then Web Developer Tools, then the Network tab.
- **Chrome and Edge:** press Ctrl+Shift+I (Cmd+Option+I on a Mac). Or open the menu, choose More tools, then Developer tools, then the Network tab.
- **Safari:** first turn on Settings, Advanced, "Show features for web developers". Then choose Develop, Show Web Inspector (Cmd+Option+I), then the Network tab.

To see that nothing is stored, open the **Storage** tab (Firefox and Safari) or the **Application** tab (Chrome and Edge). Cookies, local storage and session storage should all be empty for purelyfreefonts.com.

## What our server logs

Our web server, Caddy, keeps an access log, so that we can find errors and see which pages are used. For each request it records the time, the address asked for, the response, and some of the headers your browser sent, such as its languages. It also records the country Cloudflare adds to each request.

- **Your IP address is cut short before it is written.** An IPv4 address keeps only its first two numbers (203.0.113.7 is stored as 203.0.0.0), and an IPv6 address only its first 32 bits. What's left is shared by tens of thousands of addresses, so it can't point to yours.
- **Some details are dropped before anything is written:** the port, your browser's name and version, the page you came from, any cookies, and every header that carries your full IP address or a location finer than the country.
- **Each day's log is deleted after 14 days.**

These settings are public: see the log section of our [Caddyfile on GitHub]({{ caddyfile_url }}), and the [log rotation settings]({{ logrotate_url }}).

## What Cloudflare sees

Cloudflare sits in front of our server. It delivers every page and file and protects the server from attacks, so it sees each request in full, including your IP address. How Cloudflare uses and keeps that data is covered by [Cloudflare's privacy policy](https://www.cloudflare.com/privacypolicy/).

We don't receive Cloudflare's request logs. Its dashboard shows us only totals, such as the number of requests and of unique visitors. Once a month we note the month's totals, to see whether people use the site.

Cloudflare features that would add to what your browser runs or sends are turned off, among them:

- Web Analytics, which adds a tracking script to pages;
- Bot Fight Mode, which sets a cookie;
- Network Error Logging, which asks browsers to report connection problems to Cloudflare;
- Email Address Obfuscation and Rocket Loader, which add scripts;
- Speed Brain, which asks browsers to load pages before you open them.

Our server also marks every page "no-transform", which tells Cloudflare not to change a page on its way to you, even if one of those features were turned back on. The [header settings are on GitHub]({{ site_caddy_url }}) too.

## Coming next: your font list stays on your device

The next release will let you inventory the free fonts you have and discover new ones. Your list of fonts will never leave your device: it won't be sent to us, to Cloudflare or to anyone else.
