import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";

const basePath = process.env.SITE_BASE_PATH || "";
const clientRoot = path.resolve(import.meta.dirname, "../dist/client");
const siteRoot = path.resolve(import.meta.dirname, "../dist/site");
const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("export", Date.now().toString());
const { default: worker } = await import(workerUrl.href);
const response = await worker.fetch(
  new Request(`http://localhost${basePath}/`, { headers: { accept: "text/html" } }),
  { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
  { waitUntil() {}, passThroughOnException() {} },
);

if (!response.ok) throw new Error(`Page render failed with status ${response.status}`);

await rm(siteRoot, { recursive: true, force: true });
await mkdir(siteRoot, { recursive: true });

// Public files are emitted at the client root, while basePath-prefixed bundles
// are emitted in a nested directory. The host serves siteRoot at basePath.
for (const name of ["favicon.svg", "og.png"]) {
  await cp(path.join(clientRoot, name), path.join(siteRoot, name));
}
if (basePath) {
  await cp(path.join(clientRoot, basePath.slice(1)), siteRoot, { recursive: true });
} else {
  await cp(clientRoot, siteRoot, { recursive: true });
}

const html = await response.text();
await writeFile(path.join(siteRoot, "index.html"), html);

// Fail early if a future build stops applying the configured hosting path.
if (basePath && !html.includes(`${basePath}/_next/`)) {
  throw new Error(`Rendered HTML does not reference assets below ${basePath}`);
}

const written = await readFile(path.join(siteRoot, "index.html"), "utf8");
console.log(`Exported static site (${written.length} bytes) to ${siteRoot}`);
