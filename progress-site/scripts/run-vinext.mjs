import { spawnSync } from "node:child_process";
import path from "node:path";

const command = process.argv[2];
if (!new Set(["dev", "build", "start"]).has(command)) {
  console.error("Usage: node scripts/run-vinext.mjs <dev|build|start>");
  process.exit(2);
}

const cli = path.resolve(import.meta.dirname, "../node_modules/vinext/dist/cli.js");
const result = spawnSync(process.execPath, [cli, command], {
  stdio: "inherit",
  env: { ...process.env, WRANGLER_LOG_PATH: ".wrangler/wrangler.log" },
});

if (result.error) throw result.error;
process.exit(result.status ?? 1);
