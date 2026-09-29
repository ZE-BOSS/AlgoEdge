# Phase 6 — the Android app, release manager, push

**2026-09-29** · `mobile/` (Expo SDK 57, TypeScript, Expo Router) · `app_releases`, `investor_devices` ·
admin **App releases** tab · tests: `test_investor_app_releases.py` (4)

**How to build, sign and publish: `mobile/README.md`.** That includes the signing
key, which is the one item in the whole platform that can't be recovered if lost.

## The app

It has the same screens and rules as `investor-web`:
- sign-in and forgot password
- a lock screen
- Overview, with a touch-to-read price chart and a table view
- Money: add money (bank details with copy buttons), and withdraw (live limit,
  a reason asked for only when needed, and the account it will be paid to)
- Activity: withdrawals, money in, every unit movement, and monthly statements
  that open in the share sheet
- Trades
- Account: details, payout account (last four digits only), unlock toggle,
  password, close account, sign out, version
- How it is calculated

What makes it different from the web app:
- **Fingerprint / face / PIN unlock** on open and after 5 minutes in the
  background. It's on by default where available. Tokens stay in the secure
  store.
- **Push notifications** that mirror the investor emails that matter on a
  phone. They only show the subject line, because notifications appear on the
  lock screen.
- **The app checks for updates itself** on launch, because a sideloaded app
  gets no store updates.
- **Brand assets:** app icon, Android adaptive-icon layers (including the
  monochrome one for themed icons), splash, and a white notification icon
  (Android tints it).
- **Android settings:** `allowBackup` is off, and a blocked-permissions list
  covers what it doesn't need.

## Server side

- **Release manager** (admin → **App releases**):
  - Upload a signed APK with its version name and code, plus release notes.
  - The upload must be a real APK (checked by its zip header), under 200 MB,
    with a **version code higher than every earlier release**. Android refuses a
    downgrade, so a mistake here would strand phones that already updated.
  - The file is streamed to `data/releases/` (gitignored), and its SHA-256 is
    recorded and published next to the download.
  - "Make current" rolls back to an earlier build.
- **`GET /api/public/app`** returns the current build. The website's download
  button and the app's update prompt both use it.
  **`/api/public/app/download/{id}`** serves the file.
- **Push:**
  - `POST/DELETE /api/investor/devices` register and forget a phone. Only a
    real Expo push token is accepted.
  - A token moves to whoever signs in on that phone.
  - Expo's `DeviceNotRegistered` response removes the token.
  - Closing an account deletes all its phones, the same as its other personal
    data.
  - Push goes through the same after-commit outbox as email, so the same
    guarantee applies: nothing is sent for a change that was rolled back.
  - `PUSH_MODE=log` sends nothing.

## Verified

- **The app:**
  - `tsc --noEmit` is clean, and `eslint` passes with `eslint-config-expo`.
  - **`expo export --platform android` bundles to Hermes bytecode**, so every
    import resolves as it will on a phone.
  - Walked through as the web build at 390px against the seeded backend: sign-in,
    Overview with the chart, Add money, Withdraw (the reason box appears when
    over the limit), Activity and Account. No page errors.
  - Fixed along the way: detail rows set every value in the monospace font, so
    names and banks looked like numbers.
- **Release upload:** done through the admin UI in the browser. The website's
  endpoint then reported the new version with its size and SHA-256.
- **Backend:** 123 investor tests pass, 4 of them new:
  - upload validation (wrong extension, not a zip, version code not higher)
  - download is byte-identical
  - rollback
  - tokens move with the phone
  - a push fires on a confirmed deposit
  - uninstalled devices are dropped
  - closure forgets the phones

## What I could not do from here

- **Build the APK.** EAS builds in Expo's cloud and needs your Expo account;
  there's also no Android SDK in this environment. Steps: `mobile/README.md`.
- **See it on a real phone.** The web build shares the layouts, but biometrics,
  the secure store, push and the share sheet only exist on a device. The first
  EAS build is the real test of those.
- **Read the Expo docs.** `docs.expo.dev` is blocked by this environment's
  network policy. Every Expo API used was checked against the **installed SDK 57
  type definitions** instead. Module versions come from the SDK's own
  `bundledNativeModules.json`, the same source `npx expo install` uses; that
  command couldn't reach Expo's servers.

## Two template files removed

`create-expo-app` added:
- Expo's MIT `LICENSE` file, which doesn't belong in your proprietary app
- a `.claude/settings.json` that turned on a Claude Code plugin for everyone
  working in the repo — your call, not something to commit silently

Both are removed. Its `AGENTS.md`/`CLAUDE.md` Expo notes are kept.
