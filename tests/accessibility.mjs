import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import fs from "node:fs";
import assert from "node:assert/strict";
import { chromium } from "../.test-tools/node_modules/playwright/index.mjs";
import AxeBuilder from "../.test-tools/node_modules/@axe-core/playwright/dist/index.mjs";
const ROOT = fileURLToPath(new URL("../", import.meta.url));
const child = spawn(
  "python3",
  [
    "-m",
    "smw.app",
    "--database",
    "/tmp/smw-keyboard.sqlite",
    "serve",
    "--no-browser",
  ],
  { cwd: ROOT },
);
const address = await new Promise((resolve) =>
  child.stdout.once("data", (data) => resolve(data.toString().trim())),
);
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto(address);
  async function tabTo(selector) {
    for (let i = 0; i < 150; i++) {
      if (
        await page
          .locator(selector)
          .first()
          .evaluate((e) => e === document.activeElement)
      )
        return;
      await page.keyboard.press("Tab");
    }
    throw Error("Keyboard could not reach " + selector);
  }
  async function activate(selector) {
    await tabTo(selector);
    await page.keyboard.press("Enter");
  }
  async function text(selector, value) {
    await tabTo(selector);
    await page.keyboard.press("Control+A");
    await page.keyboard.type(value);
  }
  async function option(selector, index) {
    await tabTo(selector);
    await page.keyboard.press("Home");
    for (let i = 0; i < index; i++) await page.keyboard.press("ArrowDown");
  }
  await activate("#example");
  await page
    .getByRole("heading", { name: "Guided example", exact: true })
    .waitFor();
  await activate('[data-tab="mapping"]');
  for (const [path, disposition, target, statusIndex] of [
    ["old-service", 2, "https://example.com/service", 1],
    ["about", 1, "", 0],
    ["obsolete", 4, "", 4],
  ]) {
    await text("#search", path);
    await activate("#rows button");
    await option('#editForm [name="disposition"]', disposition);
    if (target) await text('#editForm [name="target"]', target);
    await option('#editForm [name="status"]', statusIndex);
    await text('#editForm [name="rationale"]', "Keyboard automation fixture");
    await tabTo('#editForm [name="reviewed"]');
    if (!(await page.locator('#editForm [name="reviewed"]').isChecked()))
      await page.keyboard.press("Space");
    await activate("#previewEdit");
    await activate("#saveEdit");
    await page.locator("#editDialog").waitFor({ state: "hidden" });
  }
  await activate('[data-tab="approval"]');
  await activate("#freeze");
  await page.locator("#revisions .card").first().waitFor();
  await activate('[data-tab="mapping"]');
  await activate("#rows button");
  for (let i = 0; i < 15; i++) {
    await page.keyboard.press("Tab");
    assert(
      await page.evaluate(
        () => document.activeElement.closest("dialog")?.id === "editDialog",
      ),
    );
  }
  await page.keyboard.press("Escape");
  await page.locator("#editDialog").waitFor({ state: "hidden" });
  const screens = [];
  for (const section of [
    "inventory",
    "mapping",
    "approval",
    "verification",
    "settings",
  ]) {
    await activate('[data-tab="' + section + '"]');
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    screens.push({
      section,
      violations: result.violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        description: v.description,
        nodes: v.nodes.map((n) => n.target),
      })),
    });
  }
  const serious = screens.flatMap((s) =>
    s.violations.filter((v) => ["critical", "serious"].includes(v.impact)),
  );
  fs.writeFileSync(
    ROOT + "/evidence/accessibility.json",
    JSON.stringify(
      {
        keyboard:
          "Tab/Enter/Space/Home/ArrowDown workflow completed, all three decisions reviewed and frozen; modal focus retained; Escape closes",
        automated: true,
        screenReaderHuman: false,
        filePickerOSDialogTested: false,
        screens,
      },
      null,
      2,
    ),
  );
  assert.deepEqual(serious, []);
  console.log(
    "Keyboard review/approval workflow passed; zero serious/critical axe findings across five screens.",
  );
} finally {
  await browser.close();
  child.kill("SIGTERM");
}
