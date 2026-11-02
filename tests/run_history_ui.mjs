import { chromium } from "../.test-tools/node_modules/playwright/index.mjs";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const ROOT = fileURLToPath(new URL("../", import.meta.url));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "smw-history-"));
const database = path.join(temp, "history.sqlite");
const seed = String.raw`
import sys
from smw.store import Store
from smw.domain import parse_inventory, canonical_json, now
s=Store(sys.argv[1])
p=s.create("History and remediation fixture",{"production":"https://example.invalid"})
s.import_rows(p,"old",parse_inventory(("\n".join("https://example.invalid/page-%03d"%i for i in range(250))).encode()),"Original synthetic UI fixture","synthetic")
rows=s.inventory(p)
s.edit(p,[{"id":r["id"],"patch":{"disposition":"keep","status":200,"reviewed":True,"rationale":"Original synthetic UI fixture"}} for r in rows],1)
revision=s.freeze(p)["id"]
for result in ["pass","fail","pass"]:
 with s.connection() as db:
  run=db.execute("INSERT INTO runs(revision,environment,state,created,finished,config) VALUES(?,'production','completed',?,?,?)",(revision,now(),now(),canonical_json({"ids":[rows[0]["id"]]}))).lastrowid
  db.execute("INSERT INTO observations(run,item,evidence) VALUES(?,?,?)",(run,rows[0]["id"],canonical_json({"result":result,"issues":["Original synthetic observation"],"exception":""})))
`;
const seeded = spawnSync("python3", ["-c", seed, database], {
  cwd: ROOT,
  encoding: "utf8",
});
assert.equal(seeded.status, 0, seeded.stderr);
let args = ["-m", "smw.app", "--database", database, "serve", "--no-browser"];
if (process.env.WORKBENCH_INSTALL_ROOT)
  args = [
    "-I",
    "-c",
    "import sys,runpy;sys.path.insert(0," +
      JSON.stringify(process.env.WORKBENCH_INSTALL_ROOT) +
      ");sys.argv=['migration-workbench']+sys.argv[1:];runpy.run_module('smw.app',run_name='__main__')",
    ...args.slice(2),
  ];
const child = spawn("python3", args, { cwd: ROOT });
const address = await new Promise((resolve, reject) => {
  child.stdout.once("data", (b) => resolve(b.toString().trim()));
  child.once("error", reject);
  child.once("exit", (code) => reject(Error("Server exited: " + code)));
});
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto(address);
  await page
    .getByRole("button", {
      name: "History and remediation fixture",
      exact: true,
    })
    .click();
  await page.getByRole("button", { name: "4 · Verify & handoff" }).click();
  async function inspect(id) {
    await page
      .locator("#runs .card")
      .filter({ hasText: "Run " + id + " ·" })
      .getByRole("button", { name: "Inspect", exact: true })
      .click();
    await page
      .locator("#runDetail")
      .getByText("Run " + id + " · plan", { exact: false })
      .waitFor();
  }
  for (const [id, before, change] of [
    [3, 2, "resolved"],
    [2, 1, "newly failing"],
  ]) {
    await inspect(id);
    const comparisonRequest = page.waitForRequest(
      (req) => new URL(req.url()).pathname === "/api/regression",
    );
    await page
      .getByRole("button", { name: "Compare with run " + before, exact: true })
      .click();
    const comparison = new URL((await comparisonRequest).url());
    assert.equal(comparison.searchParams.get("before"), String(before));
    assert.equal(comparison.searchParams.get("after"), String(id));
    const output = page.locator("#runDetail > pre");
    await output.waitFor();
    const differences = JSON.parse(await output.innerText());
    assert.equal(differences.find((x) => x.id === 1).change, change);
  }
  await inspect(1);
  assert.equal(
    await page.getByRole("button", { name: /Compare with run/ }).count(),
    0,
  );
  await inspect(2);
  await page.getByLabel("Remediation filter").selectOption("not checked");
  await page
    .getByRole("status")
    .filter({ hasText: "Showing 1–100 of 249" })
    .waitFor();
  const inspectedUrls = await page
    .locator("#runDetail details.card > summary")
    .allTextContents();
  const next = page.getByRole("button", {
    name: "Next remediation page",
    exact: true,
  });
  await next.focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("status")
    .filter({ hasText: "Showing 101–200 of 249" })
    .waitFor();
  inspectedUrls.push(
    ...(await page
      .locator("#runDetail details.card > summary")
      .allTextContents()),
  );
  await next.click();
  await page
    .getByRole("status")
    .filter({ hasText: "Showing 201–249 of 249" })
    .waitFor();
  inspectedUrls.push(
    ...(await page
      .locator("#runDetail details.card > summary")
      .allTextContents()),
  );
  assert.deepEqual(
    inspectedUrls.sort(),
    Array.from(
      { length: 249 },
      (_, i) =>
        "not checked · https://example.invalid/page-" +
        String(i + 1).padStart(3, "0"),
    ),
  );
  assert(await next.isDisabled());
  const tail = page
    .locator("#runDetail details.card")
    .filter({ hasText: "https://example.invalid/page-249" });
  await tail.locator(":scope > summary").click();
  await tail.getByLabel("Include in selected retry").uncheck();
  await page.getByRole("button", { name: "Previous remediation page" }).click();
  await next.click();
  await tail.locator(":scope > summary").click();
  assert.equal(
    await tail.getByLabel("Include in selected retry").isChecked(),
    false,
  );
  await page.getByLabel("Search remediation URLs").fill("page-249");
  await page
    .getByRole("status")
    .filter({ hasText: "Showing 1–1 of 1" })
    .waitFor();
  await inspect(2);
  assert.equal(
    await page.getByLabel("Search remediation URLs").inputValue(),
    "page-249",
  );
  await page.getByLabel("Search remediation URLs").fill("no-matching-url");
  await page
    .getByRole("status")
    .filter({ hasText: "0 matching rows" })
    .waitFor();
  assert(await next.isDisabled());
  await page.getByLabel("Search remediation URLs").fill("");
  let submitted;
  await page.route("**/api/run", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { id: 3 } });
  });
  await page
    .getByRole("button", { name: "Retry selected uncertain rows in new run" })
    .click();
  await page.waitForFunction(() =>
    document.querySelector("#runDetail").textContent.includes("Run 3 · plan"),
  );
  assert.deepEqual(
    [...submitted.retry].sort((a, b) => a - b),
    Array.from({ length: 248 }, (_, i) => i + 2),
  );
  assert.equal(submitted.environment, "production");
  await page.setViewportSize({ width: 390, height: 844 });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
    "Mobile remediation overflow",
  );
  assert.deepEqual(errors, []);
  fs.mkdirSync(path.join(ROOT, "evidence"), { recursive: true });
  fs.writeFileSync(
    path.join(ROOT, "evidence/run-history-ui.json"),
    JSON.stringify(
      {
        historyRuns: 3,
        olderMiddleBaseline: 1,
        oldestHasNoComparison: true,
        remediationRows: 249,
        pages: 3,
        tailDeselectionRetained: true,
        searchAndSameRunRefreshRetained: true,
        retryIdsSubmitted: 248,
        pageErrors: errors,
      },
      null,
      2,
    ),
  );
  console.log(
    "Run history direction and complete remediation navigation passed.",
  );
} finally {
  await browser.close();
  child.kill("SIGTERM");
  fs.rmSync(temp, { recursive: true, force: true });
}
