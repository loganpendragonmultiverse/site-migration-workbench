import { fileURLToPath } from "node:url";
const ROOT = fileURLToPath(new URL("../", import.meta.url));
import { chromium } from "../.test-tools/node_modules/playwright/index.mjs";
import { spawn } from "node:child_process";
import assert from "node:assert/strict";
import fs from "node:fs";
async function start(args) {
  let actual = args;
  if (
    process.env.WORKBENCH_INSTALL_ROOT &&
    args[0] === "-m" &&
    args[1] === "smw.app"
  ) {
    actual = [
      "-I",
      "-c",
      "import sys,runpy;sys.path.insert(0," +
        JSON.stringify(process.env.WORKBENCH_INSTALL_ROOT) +
        ");sys.argv=['migration-workbench']+sys.argv[1:];runpy.run_module('smw.app',run_name='__main__')",
      ...args.slice(2),
    ];
  }
  const child = spawn("python3", actual, { cwd: ROOT });
  const address = await new Promise((resolve, reject) => {
    child.stdout.once("data", (b) => resolve(b.toString().trim()));
    child.on("error", reject);
  });
  return { process: child, address };
}
const app = await start([
  "-m",
  "smw.app",
  "--database",
  "/tmp/smw-full-ui.sqlite",
  "serve",
  "--no-browser",
]);
const fixture = await start(["tests/fixture_server.py"]);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({
  acceptDownloads: true,
  viewport: { width: 1440, height: 1000 },
});
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto(app.address);
  const create = page.locator("#create");
  await create.locator('[name="name"]').fill("Full synthetic migration");
  for (const key of ["production", "staging"])
    await create.locator('[name="' + key + '"]').fill(fixture.address);
  await create.getByRole("button", { name: "Create project" }).click();
  await page
    .getByRole("heading", { name: "Full synthetic migration", exact: true })
    .waitFor();
  async function importCSV(side, text) {
    const f = page.locator("#importForm");
    await f.locator('[name="side"]').selectOption(side);
    await f.locator('[name="kind"]').selectOption("csv");
    await f.locator('[name="source"]').fill(side + " synthetic fixture");
    await f.locator('[name="file"]').setInputFiles({
      name: side + ".csv",
      mimeType: "text/csv",
      buffer: Buffer.from(text),
    });
    await f.getByRole("button", { name: "Preview import" }).click();
    await page.locator("#applyImport:not([disabled])").waitFor();
    await page.getByRole("button", { name: "Import reviewed file" }).click();
    await page.locator("#notice").filter({ hasText: "Imported" }).waitFor();
  }
  await importCSV(
    "old",
    "url,title\n" +
      ["old-service", "about", "obsolete"]
        .map((p) => fixture.address + "/" + p + "," + p)
        .join("\n"),
  );
  await importCSV(
    "new",
    "url,title\n" +
      ["service", "about"]
        .map((p) => fixture.address + "/" + p + "," + p)
        .join("\n"),
  );
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .locator('#settingsForm [name="private_allowlist"]')
    .fill("127.0.0.1");
  await page
    .getByRole("button", {
      name: "Save settings and invalidate working approval",
    })
    .click();
  await page.locator("#notice").filter({ hasText: "Settings saved" }).waitFor();
  await page.getByRole("button", { name: "2 · Decisions" }).click();
  for (const [path, decision, target, status] of [
    ["old-service", "redirect", fixture.address + "/service", "301"],
    ["about", "keep", "", "200"],
    ["obsolete", "retire", "", "410"],
  ]) {
    await page.locator("#search").fill(path);
    await page
      .locator("#rows")
      .getByRole("button", { name: "Edit", exact: true })
      .click();
    const f = page.locator("#editForm");
    await f.locator('[name="disposition"]').selectOption(decision);
    await f.locator('[name="target"]').fill(target);
    await f.locator('[name="status"]').selectOption(status);
    await f.locator('[name="rationale"]').fill("PRIVATE synthetic rationale");
    await f.locator('[name="reviewed"]').check();
    await page
      .getByRole("button", { name: "Preview changes", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Apply previewed decisions" })
      .click();
    await page.locator("#editDialog").waitFor({ state: "hidden" });
  }
  await page.getByRole("button", { name: "3 · Approval & exports" }).click();
  await page.getByRole("button", { name: "Freeze approved revision" }).click();
  await page.locator("#revisions .card").first().waitFor();
  for (const kind of ["nginx", "wordpress", "json", "csv"]) {
    const waiting = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export " + kind, exact: true })
      .click();
    const downloaded = await waiting;
    assert((await fs.promises.stat(await downloaded.path())).size > 0);
  }
  await page.getByRole("button", { name: "4 · Verify & handoff" }).click();
  async function run(environment, expected) {
    await page
      .locator('#runForm [name="environment"]')
      .selectOption(environment);
    await page
      .getByRole("button", { name: "Start declared-set check" })
      .click();
    await page
      .locator("#runDetail")
      .getByText(expected, { exact: false })
      .waitFor({ timeout: 20000 });
  }
  await run("staging", "pass: 3");
  const downloadWait = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download json", exact: true })
    .click();
  const artifact = await downloadWait;
  const raw = await fs.promises.readFile(await artifact.path(), "utf8");
  const report = JSON.parse(raw);
  assert.equal(report.denominator, 3);
  assert.equal(report.counts.pass, 3);
  assert(!raw.includes("PRIVATE synthetic rationale"));
  await fetch(fixture.address + "/set-fault");
  await run("staging", "fail: 1");
  await page.getByRole("button", { name: /Compare with run/ }).click();
  await page
    .locator("#runDetail pre")
    .filter({ hasText: "newly failing" })
    .waitFor();
  await fetch(fixture.address + "/clear-fault");
  await run("production", "fail: 2");
  await page.screenshot({
    path: ROOT + "/evidence/full-workflow.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    ROOT + "/evidence/full-ui.json",
    JSON.stringify(
      {
        engine: "chromium",
        steps: [
          "create",
          "CSV preview/import old and new",
          "explicit private fixture allowlist",
          "review all dispositions",
          "freeze",
          "download all four exports",
          "staging three passes",
          "redacted handoff",
          "regression after a real HTTP change",
          "production catches staging noindex",
        ],
        denominator: 3,
        stagingPasses: 3,
        regressionFailures: 1,
        productionNoindexFailures: 2,
        pageErrors: errors,
      },
      null,
      2,
    ),
  );
  console.log("Full UI migration and regression workflow passed.");
} finally {
  await browser.close();
  app.process.kill("SIGTERM");
  fixture.process.kill("SIGTERM");
}
