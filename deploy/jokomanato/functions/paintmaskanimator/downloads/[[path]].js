/**
 * Cloudflare Pages Function: serve installer binaries from an R2 bucket.
 *
 * Cloudflare Pages caps a *static* asset at 25 MiB, but the installers are much
 * larger, so they live in an R2 bucket (bound to the Pages project as
 * `DOWNLOADS`) and are streamed through this Function instead. The URL space is
 * unchanged — `/paintmaskanimator/downloads/<file>` — so versions.json /
 * updates.json and the in-app updater need no changes.
 *
 * The sibling `../_middleware.js` runs first and enforces the shared Basic-auth
 * password, so these downloads stay behind the site password too.
 *
 * The static download page (`downloads/index.html`) is left to the normal asset
 * handler: for the directory root or index.html this Function calls `next()`.
 */
const PREFIX = "/paintmaskanimator/downloads/";

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);

  const key = decodeURIComponent(url.pathname.slice(PREFIX.length));
  // Directory root or the static listing page: let the asset handler serve it.
  if (!key || key === "index.html") {
    return next();
  }

  if (!env.DOWNLOADS) {
    return new Response("Downloads storage (R2) is not configured.", {
      status: 503,
    });
  }

  const object = await env.DOWNLOADS.get(key);
  if (!object) {
    // Unknown file — fall through so a normal 404 is produced.
    return next();
  }

  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("etag", object.httpEtag);
  headers.set("cache-control", "public, max-age=3600");
  headers.set(
    "content-disposition",
    `attachment; filename="${key.split("/").pop()}"`,
  );
  return new Response(object.body, { headers });
}
