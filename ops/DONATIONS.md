# Donations: Stripe Payment Link

How the site's tip link is set up. The settled decisions are in [AUTHORITY.md](../AUTHORITY.md#funding); this file is the checklist.

## Facts

| | |
|---|---|
| Provider | Stripe Payment Link, "Customers choose what to pay": $0 to set up, $0 a month, no code |
| Link | `https://buy.stripe.com/dRm00beFeaJ307651Qf7i00` (live mode) |
| Amount | $5 suggested; visitors can change it, down to a $2 minimum |
| Fees on $5 | Stripe about 2.9% + 30¢ for a US card, so about 45¢. You keep about $4.55. Cards from other countries cost a little more |
| Payouts | Stripe pays out to the bank automatically |
| On the site | One plain `<a>` link to Stripe's hosted page. No Stripe script, no API keys |
| Tax | Automatic tax collection (Stripe Tax) off. If Stripe asks for a product tax category: **Nontaxable** (`txcd_00000000`). A tip buys nothing, since the list is free either way, and Stripe has no tax code for tips |
| Wording | Call it a **tip**, not a donation. Stripe lets a sole proprietor take tips for a service provided (the list), but donations only for a charitable purpose ([Stripe's rules](https://support.stripe.com/questions/requirements-for-accepting-tips-or-donations)) |

## Setup checklist

### A. Checking account (Byron, by hand)
Byron needs a checking account to accept Stripe payments.
- [x] 1. Obtain a checking account with a brick-and-mortar visit to my credit union. Get its routing and account numbers for step 2.

### B. Stripe account (Byron, by hand)
- [x] 2. Finish activating the account: business type Individual, industry Digital products → Blogs and written content (or Other digital goods), website `https://trulyfreefonts.com`, a description that says the payments are tips for the free font list (your "my coffee is not" line works), your identity, and the checking account from step 1 for payouts. Leave automatic tax collection off; product tax category Nontaxable (see Facts).
- [x] 3. Turn on two-step authentication for the Stripe login.
- [x] 4. Settings → Business → Public details: statement descriptor `TRULYFREEFONTS` (what tippers see on their card statement) and support email `admin@trulyfreefonts.com`.
- [x] 5. Settings → Customer emails: turn on receipts for successful payments.
- [x] 6. Close the Ko-fi account made earlier, or leave it idle; nothing will link to it. *(Closed by Byron on 2026-10-05, owner ruling `kofi_closed`: ko-fi.com/trulyfreefonts now redirects to Ko-fi's not-found page.)*

### C. The link (Byron, by hand)
- [x] 7. In a sandbox (test mode): Payment Links → New → **Customers choose what to pay**. Title "Buy me a coffee", one line on what the site is, preset $5, minimum $2 (a minimum makes the link less useful for testing stolen cards). Leave "Collect tax automatically" unticked. Stripe shows its own thank-you page after payment by default; a custom message is optional, under **After the payment** → Confirmation page (also editable later). Open the link (a sandbox link's URL contains `test_`), pay with test card `4242 4242 4242 4242` (any future date, any CVC), and check the payment shows up in the sandbox.
- [x] 8. Make the same link in live mode: account picker (top left) → your business account, not a sandbox. Live mode is the normal Dashboard, with no sandbox banner. Then Payment Links → New with the same settings as step 7; sandbox links don't carry over. The live URL has no `test_`, and the test card won't work on it.
- [x] 9. Send Claude the live `https://buy.stripe.com/...` link. It's a public URL, not a secret.

### D. Site (Claude, with the filterable-list release)
These steps wait for release 1 in [AUTHORITY.md](../AUTHORITY.md#releases). The current stub gets no link.
- [x] 10. Put the link in the Facts table above.
- [x] 11. Add one plain link to the footer of every page, worded **Leave a tip ($5 suggested)** (the owner's ruling of 2026-09-26): `<a href="https://buy.stripe.com/dRm00beFeaJ307651Qf7i00">Leave a tip ($5 suggested)</a>`. It loads no third-party script and sets no cookies, and the Caddyfile headers don't need to change.
- [ ] 12. Deploy with the command in [SERVER.md](SERVER.md): `ops/deploy.sh production --commit <sha>` (M2-D6; since 2026-09-30 the site deploys as releases).

### E. After launch (Byron)
- [ ] 13. The first real tip shows up in Stripe → Payments, and the payout reaches the bank.
- [ ] 14. Tips are personal income. Keep Stripe's yearly export for your tax return.

### F. The rename to Purely Free Fonts (Byron, by hand)
The owner's rulings of 2026-10-02 (AUTHORITY.md, **Name and domain**). Steps 2 and 4 above name the old site; these change them. The Payment Link's address stays the same, so the site needs no change.
- [ ] 15. Settings → Business → Business details: website `https://purelyfreefonts.com`.
- [ ] 16. Settings → Business → Public details:
  - [ ] public business name **Purely Free Fonts** (Stripe's checkout page shows it in its title, header and Link line, where it now says "trulyfreefonts.com");
  - [ ] statement descriptor `PURELYFREEFONTS`;
  - [ ] support email `admin@purelyfreefonts.com`, which Email Routing forwards to Gmail (ops/SERVER.md item 29).
- [ ] 17. Payment Links → the live link → Edit: the one line on what the site is, and the page after payment, name the new site.
- [ ] 18. Claude checks the live checkout page shows the new name.

## Verification
- `curl -s https://purelyfreefonts.com/ | grep -o 'https://buy.stripe.com/[^"]*'` prints the link.
- Clicking the link on the live site opens Stripe's payment page with $5 filled in.
- The Stripe Dashboard shows the account activated, payouts on, and two-step authentication on.
