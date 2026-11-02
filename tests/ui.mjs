import { fileURLToPath } from "node:url";
const ROOT = fileURLToPath(new URL("../", import.meta.url));
import {
  chromium,
  firefox,
} from "../.test-tools/node_modules/playwright/index.mjs";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
const processServer = spawn(
  "python3",
  [
    "-m",
    "smw.app",
    "--database",
    "/tmp/smw-ui.sqlite",
    "serve",
    "--no-browser",
  ],
  { cwd: ROOT },
);
let address = "";
await new Promise((resolve, reject) => {
  processServer.stdout.once("data", (b) => {
    address = b.toString().trim();
    resolve();
  });
  processServer.on("error", reject);
});
const results = [];
try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["firefox", firefox],
  ]) {
    const browser = await engine.launch({ headless: true });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
      acceptDownloads: true,
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(address);
    await page.getByRole("button", { name: "Start guided example" }).click();
    await page
      .getByRole("heading", { name: "Guided example", exact: true })
      .waitFor();
    await page.getByRole("button", { name: "2 · Decisions" }).click();
    await page
      .getByRole("button", {
        name: "Preview exact-path proposals",
        exact: true,
      })
      .click();
    await page
      .getByRole("button", { name: "Apply as unreviewed drafts", exact: true })
      .click();
    await page.locator("#rulesDialog").waitFor({ state: "hidden" });
    await page
      .getByRole("button", { name: "Undo last edit", exact: true })
      .click();
    await page
      .locator("#notice")
      .filter({ hasText: "Last edit restored" })
      .waitFor();
    const fixture = [
      ["old-service", "redirect", "https://example.com/service", "301"],
      ["about", "keep", "", "200"],
      ["obsolete", "retire", "", "410"],
    ];
    for (const [path, decision, target, status] of fixture) {
      await page.locator("#search").fill("/" + path);
      await page
        .locator("#rows")
        .getByRole("button", { name: "Edit", exact: true })
        .click();
      await page
        .locator('#editForm [name="disposition"]')
        .selectOption(decision);
      await page.locator('#editForm [name="target"]').fill(target);
      await page.locator('#editForm [name="status"]').selectOption(status);
      await page
        .locator('#editForm [name="rationale"]')
        .fill("Reviewed synthetic fixture");
      await page.locator('#editForm [name="reviewed"]').check();
      await page
        .getByRole("button", { name: "Preview changes", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Apply previewed decisions" })
        .click();
      await page.locator("#editDialog").waitFor({ state: "hidden" });
    }
    await page.getByRole("button", { name: "3 · Approval & exports" }).click();
    await page
      .getByRole("button", { name: "Freeze approved revision" })
      .click();
    await page.locator("#revisions .card").first().waitFor();
    const dl = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Export nginx", exact: true })
      .first()
      .click();
    const download = await dl;
    assert(
      (await fs.promises.readFile(await download.path(), "utf8")).includes(
        "return 410;",
      ),
    );
    const backupWait = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Back up workspace", exact: true })
      .click();
    const backup = await backupWait;
    assert(
      JSON.parse(await fs.promises.readFile(await backup.path(), "utf8")).sha256
        .length === 64,
    );
    await page.getByRole("button", { name: "2 · Decisions" }).click();
    await page.locator("#search").fill("old-service");
    await page
      .locator("#rows")
      .getByRole("button", { name: "Edit", exact: true })
      .click();
    await page
      .locator('#editForm [name="rationale"]')
      .fill("<img src=x onerror=alert(1)>");
    await page
      .getByRole("button", { name: "Preview changes", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Apply previewed decisions" })
      .click();
    await page.locator("#editDialog").waitFor({ state: "hidden" });
    await page.reload();
    await page
      .getByRole("button", { name: "Guided example", exact: true })
      .first()
      .click();
    await page
      .locator("#revisionStatus")
      .filter({ hasText: "changes require" })
      .waitFor();
    await page.getByRole("button", { name: "2 · Decisions" }).click();
    await page
      .getByRole("button", { name: "Undo last edit", exact: true })
      .click();
    await page
      .locator("#notice")
      .filter({ hasText: "Last edit restored" })
      .waitFor();
    await page.screenshot({
      path: ROOT + "/evidence/ui-" + name + ".png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    // Basic keyboard focus is exercised, not a human keyboard-only or screen-reader claim.
    await page.keyboard.press("Tab");
    assert(await page.evaluate(() => document.activeElement !== document.body));
    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth + 1,
      ),
    );
    results.push({
      engine: name,
      workflow:
        "guided create → decisions → approval → actual download → backup → edit → reload → undo",
      pageErrors: errors,
      automatedFocusSmoke: true,
      humanAccessibilityTest: false,
    });
    await browser.close();
  }
  fs.writeFileSync(
    ROOT + "/evidence/ui.json",
    JSON.stringify(results, null, 2),
  );
  console.log(JSON.stringify(results, null, 2));
} finally {
  processServer.kill("SIGTERM");
}
