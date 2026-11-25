# Report — iOS Universal Links broken by a wrong Apple Team ID

#### File: ios-universal-links-team-id.md
#### Author: Claude (Opus 5), investigated with Victor Anton
#### August 2026

> **Status:** 🔧 Active, 2026-11-26: the fix is applied (commit `bf27754`, on `dev` and `main`, and
> `wildlifewatcher.ai` serves `D6C4Y4J2BS`). Open, with no issue filed: the check on a real device
> and the question about `7399889N9Z` (see Open items).

**Audience:** whoever owns `frontend/public/.well-known/apple-app-site-association`.
**Goal:** make iOS password-reset links open the app instead of Safari.

---

## What is wrong

The Apple App Site Association file we serve names the wrong Apple Team ID:

```json
"appID": "7399889N9Z.com.wildlife.wildlifewatcher",
"webcredentials": { "apps": ["7399889N9Z.com.wildlife.wildlifewatcher"] }
```

The `appID` must be `<TeamID>.<BundleID>`. The Wildlife Watcher app is signed under team
**`D6C4Y4J2BS`**, not `7399889N9Z`. When the team does not match the installed app, iOS
fetches the file, rejects the association and **fails silently** — no error in the app, no
error in the browser, nothing in any log.

Verified live on 2026-08-24:

```bash
curl https://wildlifewatcher.ai/.well-known/apple-app-site-association
# → "appID": "7399889N9Z.com.wildlife.wildlifewatcher"
```

## Impact

The file declares two capabilities, and both are affected:

| Declared | Paths | Consequence |
|---|---|---|
| `applinks` | `/reset-password`, `/reset-password/*` | Password-reset links from email open **Safari instead of the app** on iOS |
| `webcredentials` | — | Shared-web-credentials password autofill does not work |

The mobile app declares its side correctly — `app.config.ts` has
`associatedDomains: ['applinks:wildlifewatcher.ai']` — so the app has always been asking for
the association. Only this file rejects it.

Android is unaffected: it uses an `intentFilter` with `autoVerify` on the same host and does
not consult this file.

## Evidence for `D6C4Y4J2BS`

Three independent sources, none of which require Apple Developer access:

1. **EAS credential records.** The App Store Connect API key used for submissions
   (`UCHGMLVTWH`, role ADMIN) belongs to team `D6C4Y4J2BS`, named
   *"Wildlife.ai (Company/Organization)"*.
2. **EAS build logs.** Every iOS build reports
   `Apple Team D6C4Y4J2BS (Wildlife.ai (Company/Organization))`.
3. **A successful submission.** Build `08cd3fe2` (v0.0.62) uploaded to App Store Connect on
   2026-08-24 using that key and team.

`7399889N9Z` appears in no EAS record, no build log, and no other repository.

## The fix

Replace both occurrences in
`frontend/public/.well-known/apple-app-site-association`:

```diff
-        "appID": "7399889N9Z.com.wildlife.wildlifewatcher",
+        "appID": "D6C4Y4J2BS.com.wildlife.wildlifewatcher",
...
-      "7399889N9Z.com.wildlife.wildlifewatcher"
+      "D6C4Y4J2BS.com.wildlife.wildlifewatcher"
```

Serving requirements (already met, worth not breaking):

- served over HTTPS from `/.well-known/apple-app-site-association`
- **no** `.json` extension, `Content-Type: application/json`
- no redirects — iOS will not follow one

## Verifying after deploy

iOS caches the association aggressively, so a stale device can look like a failed fix:

1. Confirm what is served: `curl https://wildlifewatcher.ai/.well-known/apple-app-site-association`
2. Check Apple's CDN copy, which is what devices actually fetch:
   `https://app-site-association.cdn-apple.com/a/v1/wildlifewatcher.ai`
   This can lag by up to ~24 h.
3. On a device, **delete and reinstall** the app — association is re-evaluated at install.
4. Request a password reset and confirm the emailed link opens the app rather than Safari.

## Where this came from

The same wrong team ID was in `ww-mobile-app`'s `eas.json` and broke automated iOS
submission. That was fixed in ww-mobile-app
[#244](https://github.com/wildlifeai/ww-mobile-app/pull/244), and a search for the stale
value across all four repos turned up this file as the only other place it survives.

The Apple Team ID is now recorded as a cross-repo contract in ww-mobile-app's
`.agents/skills/SKILL.md`, so the next person changing it knows both places exist.

## Open items

- [x] Apply the fix and deploy (this repo): `bf27754`, on `main`.
- [ ] Verify end-to-end on a real device per the steps above.
- [ ] Confirm with Apple Developer account access that `7399889N9Z` is not a second team
      the organisation still holds — if it is, work out what it was for. Victor regains
      account access the week of 2026-08-31.
