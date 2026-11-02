import { fileURLToPath } from "node:url";
import { chromium } from "../.test-tools/node_modules/playwright/index.mjs";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import assert from "node:assert/strict";
const ROOT = fileURLToPath(new URL("../", import.meta.url));
const database = "/tmp/smw-scale-ui.sqlite";
const seed = spawnSync(
  "python3",
  [
    "-c",
    `from smw.store import Store
from smw.domain import parse_inventory
s=Store("${database}")
p=s.create("10,000 URL fixture",{"production":"https://example.com"})
s.import_rows(p,"old",parse_inventory(("\\n".join("https://example.com/page-"+str(n) for n in range(10000))).encode()),"Original synthetic inventory","synthetic")
`,
  ],
  { cwd: ROOT },
);
assert.equal(seed.status, 0, seed.stderr.toString());
const service = spawn(
  "python3",
  ["-m", "smw.app", "--database", database, "serve", "--no-browser"],
  { cwd: ROOT },
);
const address = await new Promise((resolve) =>
  service.stdout.once("data", (b) => resolve(b.toString().trim())),
);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(address);
  const started = performance.now();
  await page
    .getByRole("button", { name: "10,000 URL fixture", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "10,000 URL fixture", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "2 · Decisions" }).click();
  await page.locator("#rows tr").first().waitFor();
  const loadMs = performance.now() - started;
  const before = performance.now();
  await page.locator("#search").fill("page-99");
  await page.locator("#rowCount").filter({ hasText: "111 matching" }).waitFor();
  const filterMs = performance.now() - before;
  await page
    .getByRole("button", { name: "Select matching rows", exact: true })
    .click();
  await page.locator("#rowCount").filter({ hasText: "111 selected" }).waitFor();
  assert.equal(await page.locator("#rows tr").count(), 100);
  assert(loadMs < 15000);
  assert(filterMs < 3000);
  const result = {
    rows: 10000,
    loadAndRenderMs: loadMs,
    filterMs,
    matched: 111,
    selected: 111,
    visibleRows: 100,
    browser: "Chromium 1.64.1 container",
    limits: "2 CPUs / 2 GiB container, no external requests",
  };
  fs.writeFileSync(
    ROOT + "/evidence/scale-ui.json",
    JSON.stringify(result, null, 2),
  );
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
  service.kill("SIGTERM");
}
