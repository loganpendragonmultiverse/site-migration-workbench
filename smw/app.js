"use strict";
const $ = (s) => document.querySelector(s);
const token = $('meta[name="workbench-token"]').content;
let activeImport = null;
let state = null,
  projectId = null,
  currentPage = 0,
  importData = null,
  editIds = [],
  preview = null,
  selected = new Set(),
  currentRun = null;
const notice = (message, error = false) => {
  $("#notice").textContent = message;
  $("#notice").classList.toggle("error", error);
};
async function api(path, data) {
  const response = await fetch("/api/" + path, {
    method: data === undefined ? "GET" : "POST",
    headers: {
      "X-Workbench-Token": token,
      ...(data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Request failed");
  return result;
}
function button(text, action) {
  const b = document.createElement("button");
  b.textContent = text;
  b.type = "button";
  b.onclick = () => perform(action);
  return b;
}
function reviewPreview(changes) {
  const byId = new Map(state.old.map((row) => [row.id, row]));
  return changes
    .map((change) => {
      const row = byId.get(change.id),
        decision = { ...row, ...change.patch };
      const destination = ["keep", "retire"].includes(decision.disposition)
        ? "Direct response"
        : decision.target || "Unresolved";
      return (
        row.url +
        "\n" +
        decision.disposition.toUpperCase() +
        " → " +
        destination +
        " | Expected HTTP " +
        (decision.disposition === "keep" ? 200 : decision.status) +
        "\nRationale: " +
        (decision.rationale || "Not supplied") +
        "\nReview: " +
        (decision.reviewed ? "Reviewed" : "Needs review") +
        (decision.excluded ? " | Excluded" : "") +
        "\n"
      );
    })
    .join("\n");
}
function paragraph(text, cls) {
  const p = document.createElement("p");
  p.textContent = text;
  if (cls) p.className = cls;
  return p;
}
async function perform(fn) {
  try {
    await fn();
  } catch (e) {
    notice(e.message, true);
  }
}
async function download(path, name) {
  const response = await fetch("/api/" + path, {
    headers: { "X-Workbench-Token": token },
  });
  if (!response.ok) {
    const e = await response.json();
    throw Error(e.error);
  }
  const blob = await response.blob(),
    a = document.createElement("a"),
    u = URL.createObjectURL(blob);
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}
function tab(name) {
  document.querySelectorAll(".tab").forEach((e) => (e.hidden = e.id !== name));
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
}
document
  .querySelectorAll("[data-tab]")
  .forEach((b) => (b.onclick = () => tab(b.dataset.tab)));
async function projects() {
  const values = await api("projects");
  $("#projects").replaceChildren(
    ...values.map((p) =>
      button(p.name + (p.archived ? " · archived" : ""), () => open(p.id)),
    ),
  );
}
async function open(id) {
  projectId = id;
  selected.clear();
  currentPage = 0;
  await refresh();
  $("#workspace").hidden = false;
  notice("Project opened. Changes autosave when applied.");
  tab("inventory");
}
async function refresh() {
  state = await api("project?id=" + projectId);
  const p = state.project;
  $("#name").textContent = p.name;
  $("#revisionStatus").textContent =
    "Working generation " +
    p.generation +
    (state.revisions[0]?.generation === p.generation
      ? " · matches latest approved revision"
      : " · changes require a new approval");
  $("#inventorySummary").textContent =
    state.old.length +
    " unique source URLs, " +
    state.new.length +
    " target URLs; " +
    state.old.reduce((n, r) => n + Math.max(0, r.provenance.length - 1), 0) +
    " duplicate source occurrences retained.";
  const f = $("#settingsForm");
  for (const k of ["production", "staging"]) f.elements[k].value = p.config[k];
  f.elements.private_allowlist.value = p.config.private_allowlist.join(", ");
  f.elements.additional_origins.value = (
    p.config.additional_origins || []
  ).join(", ");
  f.elements.staging_noindex.checked = p.config.staging_noindex;
  $("#issues").replaceChildren(
    ...(state.issues.length
      ? state.issues.map((i) =>
          paragraph("Row " + i.id + ": " + i.message, "fail"),
        )
      : [paragraph("All source decisions reviewed. Ready to freeze.", "pass")]),
  );
  $("#freeze").disabled = !!state.issues.length;
  $("#runRevision").replaceChildren(
    ...state.revisions.map((r) => {
      const o = document.createElement("option");
      o.value = r.id;
      o.textContent = "Revision " + r.id + " · generation " + r.generation;
      return o;
    }),
  );
  $("#revisions").replaceChildren(
    ...state.revisions.map((r) => {
      const d = document.createElement("div");
      d.className = "card";
      d.append(
        paragraph("Revision " + r.id + " · " + r.created),
        paragraph(r.hash, "badge"),
      );
      for (const kind of ["json", "csv", "nginx", "wordpress"])
        d.append(
          button("Export " + kind, () =>
            download(
              "export?revision=" + r.id + "&format=" + kind,
              "migration-" +
                r.id +
                "-" +
                kind +
                (kind === "nginx"
                  ? ".conf"
                  : kind === "csv"
                    ? ".csv"
                    : ".json"),
            ),
          ),
        );
      return d;
    }),
  );
  $("#runs").replaceChildren(
    ...state.runs.map((r) => {
      const d = document.createElement("div");
      d.className = "card";
      d.append(
        paragraph(
          "Run " +
            r.id +
            " · " +
            r.environment +
            " · " +
            r.state +
            (r.error ? " · " + r.error : ""),
        ),
        button("Inspect", () => showRun(r.id)),
      );
      if (r.state === "running") {
        d.append(
          button("Pause", async () => {
            await api("stop", { run: r.id, state: "paused" });
            await refresh();
          }),
          button("Cancel", async () => {
            await api("stop", { run: r.id, state: "cancelled" });
            await refresh();
          }),
        );
      } else if (r.state !== "completed")
        d.append(
          button("Resume", async () => {
            await api("resume", { run: r.id });
            await refresh();
          }),
        );
      return d;
    }),
  );
  renderRows();
}
function filtered() {
  const s = $("#search").value.toLowerCase(),
    f = $("#filter").value;
  return state.old.filter(
    (r) =>
      (r.url + " " + r.title + " " + r.rationale + " " + r.target)
        .toLowerCase()
        .includes(s) &&
      (f === "all" ||
        (f === "unresolved" && r.disposition === "unresolved") ||
        (f === "unreviewed" && !r.reviewed) ||
        (f === "excluded" && r.excluded) ||
        (f === "duplicates" && r.provenance.length > 1)),
  );
}
function renderRows() {
  const all = filtered();
  currentPage = Math.min(
    currentPage,
    Math.max(0, Math.ceil(all.length / 100) - 1),
  );
  $("#rowCount").textContent =
    all.length + " matching rows · " + selected.size + " selected";
  $("#page").textContent =
    "Page " +
    (currentPage + 1) +
    " / " +
    Math.max(1, Math.ceil(all.length / 100));
  $("#rows").replaceChildren(
    ...all.slice(currentPage * 100, currentPage * 100 + 100).map((r) => {
      const tr = document.createElement("tr"),
        select = document.createElement("td"),
        check = document.createElement("input");
      check.type = "checkbox";
      check.checked = selected.has(r.id);
      check.setAttribute("aria-label", "Select " + r.url);
      check.onchange = () => {
        check.checked ? selected.add(r.id) : selected.delete(r.id);
        $("#rowCount").textContent =
          all.length + " matching rows · " + selected.size + " selected";
      };
      select.append(check);
      tr.append(select);
      const source = document.createElement("td");
      source.append(document.createTextNode(r.url));
      const small = document.createElement("small");
      small.textContent =
        r.title +
        " · priority " +
        r.priority +
        " · " +
        r.provenance.length +
        " occurrences";
      source.append(small);
      const details = document.createElement("details"),
        summary = document.createElement("summary");
      summary.textContent = "Input provenance";
      details.append(summary);
      r.provenance.forEach((p) =>
        details.append(
          paragraph(
            p.source +
              " / row " +
              p.row +
              " / " +
              p.original +
              " / " +
              p.imported +
              " / SHA256 " +
              p.fingerprint,
          ),
        ),
      );
      if (r.canonical)
        details.append(paragraph("Supplied canonical: " + r.canonical));
      source.append(details);
      tr.append(source);
      for (const text of [
        r.disposition + (r.excluded ? " · excluded" : ""),
        r.target || "—",
        r.reviewed ? "Reviewed" : "Needs review",
      ]) {
        const td = document.createElement("td");
        td.textContent = text;
        tr.append(td);
      }
      const action = document.createElement("td");
      action.append(button("Edit", () => edit([r.id])));
      tr.append(action);
      return tr;
    }),
  );
}
async function edit(ids) {
  if (!ids.length) throw Error("Select rows first");
  editIds = ids;
  preview = null;
  $("#saveEdit").disabled = true;
  $("#editPreview").textContent = "";
  $("#suggestions").replaceChildren();
  const f = $("#editForm"),
    r = state.old.find((r) => r.id === ids[0]);
  f.reset();
  for (const k of [
    "disposition",
    "target",
    "status",
    "priority",
    "rationale",
    "exception",
  ])
    f.elements[k].value = r[k];
  for (const k of ["reviewed", "excluded"]) f.elements[k].checked = !!r[k];
  $("#editScope").textContent =
    ids.length === 1
      ? r.url
      : "Bulk edit: " +
        ids.length +
        " selected source URLs. The same decision will apply to every selected row. Use merge for intentional many-to-one destinations.";
  $("#editDialog").showModal();
  if (ids.length === 1) {
    const suggestions = await api(
      "suggestions?project=" + projectId + "&id=" + ids[0],
    );
    $("#suggestions").append(
      paragraph("Explainable candidates; always review"),
    );
    suggestions.forEach((s) =>
      $("#suggestions").append(
        button(s.url + " · " + s.reason, () => {
          f.elements.target.value = s.url;
          f.elements.disposition.value = "redirect";
          preview = null;
          $("#saveEdit").disabled = true;
        }),
      ),
    );
  }
}
function patch() {
  const f = $("#editForm");
  return {
    disposition: f.elements.disposition.value,
    target: f.elements.target.value,
    status: Number(f.elements.status.value),
    priority: Number(f.elements.priority.value),
    rationale: f.elements.rationale.value,
    exception: f.elements.exception.value,
    reviewed: f.elements.reviewed.checked,
    excluded: f.elements.excluded.checked,
  };
}
$("#editForm").oninput = () => {
  $("#saveEdit").disabled = true;
  preview = null;
};
$("#previewEdit").onclick = () => {
  preview = {
    project: projectId,
    generation: state.project.generation,
    changes: editIds.map((id) => ({ id, patch: patch() })),
  };
  $("#editPreview").textContent = reviewPreview(preview.changes);
  $("#saveEdit").disabled = false;
};
$("#editForm").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    if (!preview) throw Error("Preview changes first");
    await api("edit", preview);
    $("#editDialog").close();
    selected.clear();
    await refresh();
    notice("Decisions saved. Undo is available.");
  });
};
$("#cancelEdit").onclick = () => $("#editDialog").close();
$("#bulk").onclick = () => perform(() => edit([...selected]));
$("#undo").onclick = () =>
  perform(async () => {
    await api("undo", { project: projectId });
    await refresh();
    notice("Last edit restored; working approval needs renewal.");
  });
$("#search").oninput = () => {
  currentPage = 0;
  renderRows();
};
$("#filter").onchange = () => {
  currentPage = 0;
  renderRows();
};
$("#prev").onclick = () => {
  currentPage = Math.max(0, currentPage - 1);
  renderRows();
};
$("#next").onclick = () => {
  currentPage++;
  renderRows();
};
$("#create").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    const f = e.target;
    const r = await api("create", {
      name: f.elements.name.value,
      config: {
        production: f.elements.production.value,
        staging: f.elements.staging.value,
      },
    });
    await projects();
    await open(r.id);
  });
};
$("#example").onclick = () =>
  perform(async () => {
    const r = await api("example", {});
    await projects();
    await open(r.id);
    notice(
      "Example: map old-service to /service, keep /about, retire /obsolete with 410. Then review and freeze. Example URLs are synthetic; verification would contact those sites.",
    );
  });
async function fileContent(file) {
  if (file.size > 10 * 1024 * 1024) throw Error("File exceeds 10 MiB");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = "";
  for (let i = 0; i < bytes.length; i += 8192)
    text += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(text);
}
$("#importForm").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    const f = e.target;
    importData = {
      project: projectId,
      side: f.elements.side.value,
      source: f.elements.source.value,
      kind: f.elements.kind.value,
      content: await fileContent(f.elements.file.files[0]),
      columns: Object.fromEntries(
        ["url", "title", "priority", "canonical"].map((k) => [
          k,
          f.elements[k].value,
        ]),
      ),
    };
    const p = await api("preview", importData);
    $("#importPreview").textContent =
      p.total +
      " rows; showing up to 100\n" +
      p.rows
        .map(
          (row) =>
            "Source row " +
            row.row +
            ": " +
            row.url +
            "\nTitle: " +
            (row.title || "Not supplied") +
            " | Priority: " +
            row.priority +
            (row.canonical ? "\nSupplied canonical: " + row.canonical : ""),
        )
        .join("\n\n");
    $("#applyImport").disabled = false;
  });
};
$("#importForm").oninput = () => {
  importData = null;
  $("#applyImport").disabled = true;
};
$("#applyImport").onclick = () =>
  perform(async () => {
    activeImport = crypto.randomUUID();
    $("#cancelImport").disabled = false;
    const r = await api("import", { ...importData, job: activeImport });
    $("#cancelImport").disabled = true;
    activeImport = null;
    importData = null;
    $("#applyImport").disabled = true;
    await refresh();
    notice("Imported " + r.imported + " source occurrences.");
  });
$("#fetchForm").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    const f = e.target;
    notice("Fetching bounded sitemap…");
    activeImport = crypto.randomUUID();
    $("#cancelImport").disabled = false;
    const r = await api("fetch", {
      project: projectId,
      url: f.elements.url.value,
      side: f.elements.side.value,
      job: activeImport,
    });
    $("#cancelImport").disabled = true;
    activeImport = null;
    await refresh();
    notice("Imported " + r.imported + " sitemap rows.");
  });
};
$("#freeze").onclick = () =>
  perform(async () => {
    const r = await api("freeze", { project: projectId });
    await refresh();
    notice("Frozen revision " + r.id + " · " + r.hash);
  });
$("#archive").onclick = () =>
  perform(async () => {
    await api("archive", {
      project: projectId,
      archived: !state.project.archived,
    });
    await refresh();
    await projects();
    notice(
      state.project.archived
        ? "Project archived; data preserved."
        : "Project reopened.",
    );
  });
$("#settingsForm").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    const f = e.target;
    await api("configure", {
      project: projectId,
      config: {
        production: f.elements.production.value,
        staging: f.elements.staging.value,
        additional_origins: f.elements.additional_origins.value
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
        private_allowlist: f.elements.private_allowlist.value
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
        staging_noindex: f.elements.staging_noindex.checked,
      },
    });
    await refresh();
    notice("Settings saved. Future approval will include them.");
  });
};
$("#runForm").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    const f = e.target;
    if (!f.elements.revision.value) throw Error("Freeze a plan first");
    const r = await api("run", {
      revision: Number(f.elements.revision.value),
      environment: f.elements.environment.value,
    });
    await refresh();
    await showRun(r.id);
    notice("Run " + r.id + " started.");
  });
};
async function showRun(id) {
  currentRun = id;
  const r = await api("report?id=" + id),
    d = $("#runDetail");
  d.replaceChildren();
  d.append(
    paragraph("Run " + id + " · plan " + r.plan_hash),
    paragraph(
      "Denominator " +
        r.denominator +
        " · " +
        Object.entries(r.counts)
          .map(([k, v]) => k + ": " + v)
          .join(" / "),
    ),
    paragraph(r.limitations),
  );
  const sharing = document.createElement("input");
  sharing.type = "checkbox";
  sharing.checked = true;
  sharing.id = "shareMode";
  const label = document.createElement("label");
  label.append(
    sharing,
    document.createTextNode(
      "Redacted sharing mode (remove query values, notes, headers and sensitive evidence)",
    ),
  );
  d.append(label);
  for (const fmt of ["html", "csv", "json"])
    d.append(
      button("Download " + fmt, () =>
        download(
          "handoff?run=" +
            id +
            "&format=" +
            fmt +
            "&sharing=" +
            sharing.checked,
          "handoff-" + id + "." + fmt,
        ),
      ),
    );
  const uncertain = r.rows
    .filter(
      (x) =>
        ["inconclusive", "not checked"].includes(x.result) &&
        x.result !== "excluded",
    )
    .map((x) => x.id);
  const retrySelection = new Set(uncertain);
  d.append(
    button("Retry selected uncertain rows in new run", async () => {
      if (!retrySelection.size) throw Error("Select uncertain rows first");
      const n = await api("run", {
        revision: r.revision,
        environment: r.environment,
        retry: [...retrySelection],
      });
      await refresh();
      await showRun(n.id);
    }),
  );
  const previous = state.runs.find(
    (x) =>
      x.id !== id &&
      x.revision === r.revision &&
      x.environment === r.environment,
  );
  if (previous)
    d.append(
      button("Compare with run " + previous.id, async () => {
        const results = await api(
          "regression?before=" + previous.id + "&after=" + id,
        );
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(
          results.filter((x) => x.change !== "unchanged"),
          null,
          2,
        );
        d.append(pre);
      }),
    );
  const filter = document.createElement("select");
  filter.setAttribute("aria-label", "Remediation filter");
  [
    "needs attention",
    "all",
    "fail",
    "warning",
    "inconclusive",
    "not checked",
    "pass",
    "excluded",
  ].forEach((v) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    filter.append(o);
  });
  d.append(filter);
  const list = document.createElement("div");
  d.append(list);
  function draw() {
    const values = r.rows.filter(
      (x) =>
        filter.value === "all" ||
        (filter.value === "needs attention" &&
          !["pass", "excluded"].includes(x.result)) ||
        x.result === filter.value,
    );
    list.replaceChildren(
      paragraph(values.length + " rows; showing first 100"),
      ...values.slice(0, 100).map((x) => {
        const card = document.createElement("details");
        card.className = "card";
        const summary = document.createElement("summary");
        summary.textContent = x.result + " · " + x.url;
        card.append(summary);
        if (["not checked", "inconclusive"].includes(x.result)) {
          const label = document.createElement("label"),
            check = document.createElement("input");
          check.type = "checkbox";
          check.checked = retrySelection.has(x.id);
          check.onchange = () =>
            check.checked
              ? retrySelection.add(x.id)
              : retrySelection.delete(x.id);
          label.append(
            check,
            document.createTextNode("Include in selected retry"),
          );
          card.append(label);
        }
        const facts = document.createElement("dl");
        const values = [
          ["Decision", x.disposition],
          ["Expected destination", x.target || x.url],
          ["Expected HTTP", x.expected_status],
          ["Observed destination", x.final_url],
          ["Observed HTTP", x.status],
          ["Issues", (x.issues || []).join("; ")],
          ["Supplied source title", x.supplied_title],
          ["Supplied target title", x.supplied_target_title],
          ["Observed title", x.title],
          ["Supplied canonical", x.supplied_canonical],
          ["Observed canonical", (x.canonicals || []).join("; ")],
          ["Observed at", x.observed_at],
          ["Priority", x.priority],
          ["Approved exception", x.exception],
          ["Observation error", x.error],
        ];
        for (const [name, value] of values) {
          if (value === undefined || value === "") continue;
          const term = document.createElement("dt"),
            description = document.createElement("dd");
          term.textContent = name;
          description.textContent = String(value);
          facts.append(term, description);
        }
        card.append(facts);
        const raw = document.createElement("details"),
          rawTitle = document.createElement("summary"),
          pre = document.createElement("pre");
        rawTitle.textContent = "Raw evidence";
        pre.textContent = JSON.stringify(x, null, 2);
        raw.append(rawTitle, pre);
        card.append(raw);
        return card;
      }),
    );
  }
  filter.onchange = draw;
  draw();
}
$("#backup").onclick = () =>
  perform(() => download("backup", "migration-backup.json"));
$("#restore").onchange = () =>
  perform(async () => {
    const file = $("#restore").files[0];
    if (file.size > 20 * 1024 * 1024)
      throw Error(
        "UI restore accepts up to 20 MiB; use the CLI for larger backups",
      );
    const r = await api("restore", JSON.parse(await file.text()));
    notice("Restored to " + r.path + ". " + r.instruction);
  });
setInterval(() => {
  if (projectId && state?.runs.some((r) => r.state === "running"))
    perform(async () => {
      await refresh();
      if (currentRun) await showRun(currentRun);
    });
}, 3000);
perform(projects);

$("#cancelImport").onclick = () =>
  perform(async () => {
    if (activeImport) {
      await api("cancel-import", { job: activeImport });
      notice(
        "Cancellation requested. Current request has a bounded deadline; uncommitted import rows roll back.",
      );
    }
  });

let ruleChanges = [];
$("#pathRules").onclick = () =>
  perform(async () => {
    const rawPath = (value) => value.replace(/^https?:\/\/[^/]+/, "") || "/";
    const targets = new Map();
    for (const row of state.new) {
      const key = rawPath(row.url);
      if (!targets.has(key)) targets.set(key, []);
      targets.get(key).push(row);
    }
    ruleChanges = [];
    for (const row of state.old) {
      if (
        row.excluded ||
        (selected.size && !selected.has(row.id)) ||
        (!selected.size && row.disposition !== "unresolved")
      )
        continue;
      const matches = targets.get(rawPath(row.url)) || [];
      if (matches.length === 1) {
        const target = matches[0].url;
        ruleChanges.push({
          id: row.id,
          patch: {
            disposition: target === row.url ? "keep" : "redirect",
            target: target === row.url ? "" : target,
            status: 301,
            reviewed: false,
            rationale:
              "Draft exact path-and-query proposal; content equivalence requires review",
          },
        });
      }
    }
    if (!ruleChanges.length)
      throw Error("No unique exact path-and-query proposals in this scope.");
    $("#rulesPreview").textContent = reviewPreview(ruleChanges);
    $("#rulesDialog").showModal();
  });
$("#applyRules").onclick = () =>
  perform(async () => {
    await api("edit", {
      project: projectId,
      generation: state.project.generation,
      changes: ruleChanges,
    });
    $("#rulesDialog").close();
    await refresh();
    notice("Path proposals saved as unreviewed drafts.");
  });
$("#cancelRules").onclick = () => $("#rulesDialog").close();

$("#selectMatching").onclick = () => {
  for (const row of filtered()) selected.add(row.id);
  renderRows();
};
$("#clearSelection").onclick = () => {
  selected.clear();
  renderRows();
};

$("#editForm").elements.disposition.addEventListener("change", () => {
  const f = $("#editForm"),
    choice = f.elements.disposition.value;
  f.elements.status.value =
    choice === "keep" ? "200" : choice === "retire" ? "410" : "301";
  if (["keep", "retire"].includes(choice)) f.elements.target.value = "";
});

for (const modal of document.querySelectorAll("dialog")) {
  modal.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || event.ctrlKey || event.altKey || event.metaKey)
      return;
    const controls = [
      ...modal.querySelectorAll("button,input,select,textarea,[tabindex]"),
    ].filter(
      (el) => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length,
    );
    if (!controls.length) return;
    const first = controls[0],
      last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}
