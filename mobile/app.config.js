/**
 * Dynamic Expo config layered over app.json.
 *
 * Exists for exactly one reason: `experiments.baseUrl` must apply to the web
 * export and to nothing else.
 *
 * GitHub Pages serves a project site under /<repo>, so the web bundle needs
 * that prefix baked into its asset URLs. But baseUrl is not web-scoped -- with
 * it set in app.json, the *native* Android bundle also had its asset URLs
 * rewritten, producing entries like
 *
 *     http://localhost:8081/paxcast/assets/node_modules/@expo-google-fonts/...
 *
 * for the Archivo faces and the vector-icon fonts. Those are the assets
 * app/_layout.tsx holds the splash screen on, and the whole Modernist type
 * scale depends on the 800 weight resolving.
 *
 * So the prefix is opt-in per build: only the Pages workflow sets the variable.
 */

module.exports = ({ config }) => {
  const baseUrl = process.env.PAXCAST_WEB_BASE_URL;
  if (baseUrl) {
    config.experiments = { ...(config.experiments || {}), baseUrl };
  }
  return config;
};
