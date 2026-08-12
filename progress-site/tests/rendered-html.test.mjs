import assert from "node:assert/strict";
import test from "node:test";
import report from "../app/feature-data.json" with { type: "json" };

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const basePath = process.env.SITE_BASE_PATH || "";
  return worker.fetch(
    new Request(`http://localhost${basePath}/`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the feature verification map", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  const statements = report.features.reduce((total, feature) => total + feature.statements, 0);
  const covered = report.features.reduce((total, feature) => total + feature.covered, 0);
  const coverage = Math.round(covered / statements * 100);

  assert.match(html, /<title>PaintMaskAnimator — 機能検証マップ<\/title>/);
  assert.match(html, /<h1>機能検証マップ<\/h1>/);
  assert.match(html, new RegExp(`${coverage}(?:<!-- -->)?%`));
  assert.match(html, new RegExp(statements.toLocaleString("en-US")));
  assert.match(html, /未分類モジュール/);
  assert.match(html, /https:\/\/jokomanato\.com\/paintmaskanimator\/og\.png/);
  assert.doesNotMatch(html, /http:\/\/localhost/);
  assert.doesNotMatch(html, /codex-preview|Building your site|react-loading-skeleton/i);
});

test("renders every feature and its coverage evidence", async () => {
  const html = await (await render()).text();
  for (const feature of report.features) {
    assert.match(html, new RegExp(`aria-label="${feature.name} 行カバレッジ${feature.coverage}%"`));
  }
  assert.match(html, /この数値が意味すること/);
  assert.match(html, /仕様の完成度そのものではなく/);
});
