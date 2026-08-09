# Building and distributing PaxCast for Android

Everything in the repo is release-ready except the two things that require your
accounts: a build service and a distribution channel. This document covers both.

---

## Why there is no APK in this repo

An APK cannot be produced without the Android SDK, and the SDK's build
dependencies (the Android Gradle Plugin, androidx) come from Google's Maven
repository. Any environment without both will fail at the Gradle step, no matter
how complete the JavaScript side is. The bundle itself builds fine — that part is
verified in CI before the cloud build starts.

You have two routes. EAS is the fast one; a local build is the one with no
external dependency.

---

## Route A — EAS Build (recommended)

Expo's cloud builder. Free tier is adequate for a prototype.

```bash
npm install -g eas-cli
eas login
cd mobile
eas init                 # writes your real projectId into app.json
eas build --platform android --profile preview
```

The `preview` profile in `eas.json` produces an **APK** (`buildType: "apk"`),
which is what you want for something installable by hand. The `production`
profile produces an **AAB**, because Google Play does not accept APKs for new
apps.

When the build finishes, EAS gives you a download URL and a QR code. That URL is
already a public distribution channel — anyone with the link can install it.

### Point it at a real API first

A release build must not ship pointing at `10.0.2.2`, which is the Android
emulator's alias for the host loopback and resolves to nothing on a real device.
The API base is build-time configurable:

```bash
EXPO_PUBLIC_API_BASE_URL=https://your-api.example.com \
  eas build --platform android --profile preview
```

Or set it permanently in the profile's `env` block in `eas.json`.

**The backend has to be deployed and reachable over HTTPS**, or the app installs
and then fails on every screen. Android blocks cleartext HTTP by default, so an
`http://` API will be refused even if it is reachable.

---

## Route B — Local build (no Expo account)

```bash
cd mobile
npx expo prebuild --platform android   # generates the native android/ project
cd android
./gradlew assembleRelease
```

Output: `android/app/build/outputs/apk/release/app-release.apk`

Requires Android Studio or the command-line SDK tools, plus a signing keystore
you generate and keep yourself:

```bash
keytool -genkey -v -keystore paxcast.keystore -alias paxcast \
        -keyalg RSA -keysize 2048 -validity 10000
```

Never commit the keystore or its passwords. Losing the keystore means you can
never update an app already published under it.

---

## Distribution

| Channel | Effort | Reach | Notes |
|---|---|---|---|
| **EAS build URL** | none | anyone with the link | Simplest public route. Expires after a period. |
| **GitHub Releases** | low | anyone | Wired up in `.github/workflows/android.yml` — push a `v*` tag and the APK is attached to the release. |
| **Play Store internal testing** | medium | up to 100 named testers | Needs a Play developer account (one-off $25). |
| **Play Store closed testing** | medium | 20+ testers, 14 days | Google now requires this before production for new personal accounts. |
| **Play Store production** | high | public | Requires everything above plus a privacy policy and data-safety disclosure. |

### The GitHub Actions route

`.github/workflows/android.yml` builds on every `v*` tag and attaches the APK to
a GitHub Release. Two repository secrets are needed:

- `EXPO_TOKEN` — expo.dev → Account settings → Access tokens
- `API_BASE_URL` — the public HTTPS URL of your deployed API

```bash
git tag v0.1.0 && git push --tags
```

The workflow typechecks and bundles *before* invoking EAS, so a broken import
fails in about a minute rather than fifteen.

---

## Before making this public

These are not formalities. Read them.

### 1. The data is synthetic

Passenger forecasts run on a **synthesised schedule**. The structure is
realistic — carrier mix, banking, frequency patterns, anchored to published
annual totals — but the individual flights are invented. Nothing in the app is
built on a licensed schedule feed.

### 2. Checkpoint wait times are unvalidated until reported

A new checkpoint's parameters come from `suggest_prior_from_throughput()`, whose
constants are engineering estimates, not fitted values. Its own docstring says
so. Convergence to truth needs on the order of 100+ reports.

**Concretely: a traveller who trusts a fresh checkpoint's number to decide when
to leave for the airport could miss a flight.** The design screens use SEA
(Seattle–Tacoma) with entirely fabricated waits. Publishing that as-is presents
invented security times for a real airport to real travellers.

Pick one before any public release:

- Label it unambiguously as a prototype in the store listing, the release notes,
  and in-app on first run
- Replace real airport identities with fictional ones for the demo build
- Restrict to internal/closed testing until real observations exist
- Ship only the operator-facing forecasting side, which does not make
  time-critical personal claims

The release notes in the CI workflow already carry a warning. That is a
mitigation, not a substitute for the decision.

### 3. Play Store requirements

- **Privacy policy URL** is mandatory. The app collects wait-time reports with
  timestamps — that is user-contributed content and must be disclosed.
- **Data safety form**: declare that observations are transmitted to your server.
  There is no advertising ID, no location access, and no account.
- **Permissions**: `INTERNET` only. The earlier `ACCESS_COARSE_LOCATION` was
  removed because nothing requests it, and an unused permission is both a review
  flag and a reason for users to decline.
- **Target API level** must meet Google's current minimum; Expo SDK 52 does.

### 4. Trademark

"PaxCast" is not cleared. Check before publishing under it.

---

## Pre-flight checklist

```
[ ] Backend deployed and reachable over HTTPS
[ ] EXPO_PUBLIC_API_BASE_URL points at it, not 10.0.2.2
[ ] eas init has replaced REPLACE_WITH_YOUR_EAS_PROJECT_ID
[ ] Keystore generated and backed up somewhere you will not lose it
[ ] Prototype/limitations warning visible on first run
[ ] Privacy policy published, if going to Play
[ ] Decision made on real vs fictional airport identities
```
