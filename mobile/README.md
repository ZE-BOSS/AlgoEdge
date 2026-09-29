# Alphavantiq — the Android app

Expo (SDK 57) and React Native, TypeScript, Expo Router. It uses the same
`/api/investor` routes as `investor-web`.

The app is **sideloaded**, not on the Play Store:
- you build a signed APK with EAS
- you upload it in the admin console
- the website's download button and the app's own update prompt then serve it

## Working on it

```
cd mobile
npm install
npx expo start            # scan the QR code with a development build on your phone
npm run typecheck && npm run lint
```

- In development the app talks to `http://localhost:8000`. A phone can't reach
  your laptop's `localhost`, so start the dev server with
  `EXPO_PUBLIC_API_URL=http://<your-laptop-ip>:8000`.
- Add native modules with `npx expo install <package>`, which picks
  SDK-matched versions.
- `AGENTS.md` / `CLAUDE.md` are Expo's notes for AI coding assistants. They're
  kept because they're accurate for SDK 57.

## Building a release

You need a free Expo account, once.

1. `npx eas-cli@latest login`
2. `npx eas-cli@latest init`. This writes `extra.eas.projectId` into `app.json`
   (commit that change). **Push notifications need it**; without it the app
   runs fine but never registers for notifications.
3. Bump **both** version numbers in `app.json`:
   - `expo.version`: what people see, e.g. `1.0.1`.
   - `expo.android.versionCode`: a whole number that **must go up every
     release**. Android refuses to install a lower one over a higher one, and the
     admin upload refuses it too.
4. `npm run build:apk`
   - This is `eas build -p android --profile production`. It builds in Expo's
     cloud and produces a signed `.apk`.
   - The API address baked in is `EXPO_PUBLIC_API_URL` from `eas.json`:
     `https://api.alphavantiqcapital.com`.
   - It first uploads the project. That upload should be about **1 MB**. EAS
     packs the whole git repository, so the root `.easignore` limits it to
     `mobile/`. If it ever says hundreds of MB, that file is missing or out of
     date; as a fallback, run the build with the repository ignored entirely:
     `$env:EAS_NO_VCS=1; npm run build:apk` (PowerShell).
5. Download the APK from the link EAS prints. Expect roughly 60 to 90 MB: one
   file that runs on every Android phone.
6. Publish it: **Admin console → Investors → App releases**. Upload the file,
   enter the same version name and code, add a line of release notes, then
   **Upload and publish**.

That's the whole release. On their next launch, every installed copy offers the
update, and the website's download button points at the new file.

## The signing key: the one thing that cannot be recovered

Android only installs an update that is **signed with the same key** as the app
already on the phone. On the first build, EAS generates that key and keeps it
for you.

**If the key is lost:**
- no update can ever install over an existing copy
- every investor would have to uninstall and reinstall by hand

**So, right after the first build:**

```
npx eas-cli@latest credentials -p android
```

1. Choose **Download credentials** / keystore. That gives you a `.jks` file and
   its passwords.
2. Store the `.jks` and the passwords in your password manager, **and** in a
   second place you control, such as an encrypted drive.
3. Never commit it: `*.jks` is gitignored.

If you ever move away from EAS, that same keystore is what you sign with
locally.

## What the app does

- **Sign-in** with the investor's email and password. Invitations and password
  resets use the emailed link, which opens in the browser; then they sign in
  here.
- **Unlock with fingerprint, face or device PIN** when the app opens and after
  5 minutes in the background. It's on by default where the phone supports it,
  and can be turned off in Account.
  - The session tokens live in the Android Keystore-backed secure store.
    Biometrics decide whether the app shows anything, not whether the tokens
    exist.
- **Overview, Money (add/withdraw), Activity (with monthly statements to open
  or share), Trades, Account, How it is calculated**. Same rules and wording as
  the web app.
- **Push notifications**: money received, withdrawal approved/paid/declined,
  statement ready.
  - The notification shows the subject line only, never more detail, because it
    appears on the lock screen.
  - Every one is also an email, so turning notifications off loses nothing.
- **Update check** on launch: if the published `versionCode` is higher than the
  installed one, it offers the download.
- Uninstalled phones are dropped from the push list automatically. Signing out
  unregisters the phone. Closing an account deletes all its phones.

## Installing, for investors

The website's app section says this; it's repeated here for support calls.

1. On the Android phone, open **alphavantiqcapital.com → The investor app →
   Download for Android**.
2. Open the downloaded file. Android will ask to allow installs from the
   browser ("Install unknown apps"). Allow it for this install.
3. Open **Alphavantiq** and sign in.

On iPhone there is no app without an Apple developer account. Investors open
`app.alphavantiqcapital.com` in Safari and choose **Share → Add to Home Screen**.
