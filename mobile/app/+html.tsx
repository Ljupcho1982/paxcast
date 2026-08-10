/**
 * Custom document for the static web export.
 *
 * Exists to make the site installable. iOS has no sideloading and no APK
 * equivalent, so "Add to Home Screen" from Safari is the only way a PaxCast
 * icon lands on an iPhone without a $99 Apple Developer account. That path
 * needs an apple-touch-icon and the apple-mobile-web-app-* meta tags; Safari
 * ignores the web manifest's icons entirely.
 */

import { ScrollViewStyleReset } from 'expo-router/html';
import type { PropsWithChildren } from 'react';

// GitHub Pages serves the site under /<repo>. The manifest and icons are
// absolute paths, so they need that prefix; local builds leave it empty.
const BASE = process.env.EXPO_PUBLIC_WEB_BASE_URL ?? '';

export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        {/* viewport-fit=cover lets safe-area insets resolve, so the layout
            clears the notch and the home indicator once installed. */}
        <meta
          name="viewport"
          content="width=device-width, initial-scale=1, shrink-to-fit=no, viewport-fit=cover"
        />

        <title>PaxCast</title>
        <meta
          name="description"
          content="Probabilistic airport passenger-throughput forecasts. Reports a distribution, not a single number."
        />

        <link rel="manifest" href={`${BASE}/manifest.json`} />
        <meta name="theme-color" content="#0B1020" />

        {/* Safari reads these, not the manifest. */}
        <link rel="apple-touch-icon" href={`${BASE}/icons/apple-touch-icon.png`} />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-title" content="PaxCast" />
        {/* black-translucent lets the app background run under the status bar,
            which matches the dark theme rather than banding it with white. */}
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />

        <ScrollViewStyleReset />

        {/* Painted before the bundle evaluates, so launching from the home
            screen does not flash white before the dark theme mounts. */}
        <style dangerouslySetInnerHTML={{ __html: `html,body{background-color:#0B1020;}` }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
