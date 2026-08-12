/**
 * Cloudflare Pages Function: HTTP Basic Auth for the ENTIRE PaintMaskAnimator
 * path — every page, plus the installer downloads and the update manifest.
 *
 * Deployed into the jokomanato.com site repository at
 * `functions/paintmaskanimator/_middleware.js`; it therefore runs for every
 * request under `/paintmaskanimator/`.
 *
 * The shared password is read from the `SITE_PASSWORD` (and optional
 * `SITE_USERNAME`, default "guest") environment variables configured on the
 * Cloudflare Pages project — nothing secret is committed here. The in-app
 * updater authenticates with the SAME shared credentials (baked into the app's
 * constants), so `downloads/` and `updates.json` no longer need to be public.
 */
function unauthorized() {
  return new Response("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="PaintMaskAnimator", charset="UTF-8"',
    },
  });
}

// Constant-time-ish comparison to avoid trivially leaking length/prefix.
function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

export async function onRequest(context) {
  const { request, env, next } = context;

  const expectedPassword = env.SITE_PASSWORD;
  // If no password is configured, fail closed rather than exposing the site.
  if (!expectedPassword) {
    return new Response("Site password is not configured.", { status: 503 });
  }
  const expectedUser = env.SITE_USERNAME || "guest";

  const header = request.headers.get("Authorization") || "";
  if (header.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(header.slice("Basic ".length));
    } catch {
      return unauthorized();
    }
    const sep = decoded.indexOf(":");
    const user = sep === -1 ? decoded : decoded.slice(0, sep);
    const pass = sep === -1 ? "" : decoded.slice(sep + 1);
    if (safeEqual(user, expectedUser) && safeEqual(pass, expectedPassword)) {
      return next();
    }
  }
  return unauthorized();
}
