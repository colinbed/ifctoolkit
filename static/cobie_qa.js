(function () {
  const form = document.getElementById("cobieQaForm");
  if (!form) return;

  const byId = (id) => document.getElementById(id);
  const issueBody = document.querySelector("#cobieQaIssueTable tbody");
  let jobId = null;
  let allIssues = [];

  function progress(value, stage, message) {
    const percent = Math.round(Math.max(0, Math.min(1, value)) * 100);
    byId("cobieQaProgressPct").textContent = `${percent}%`;
    byId("cobieQaProgressBar").style.width = `${percent}%`;
    byId("cobieQaStage").textContent = stage;
    byId("cobieQaStatus").textContent = message;
  }

  function appendTextCell(row, value, code = false) {
    const cell = document.createElement("td");
    const target = code ? document.createElement("code") : cell;
    target.textContent = value === null || value === undefined ? "" : String(value);
    if (code) cell.appendChild(target);
    row.appendChild(cell);
  }

  function renderIssues() {
    const severity = byId("filterSeverity").value;
    const explicitSeverities = [];
    if (byId("filterErrorsOnly").checked) explicitSeverities.push("Error");
    if (byId("filterWarningsOnly").checked) explicitSeverities.push("Warning");
    const sheet = byId("filterSheet").value;
    const ruleType = byId("filterRuleType").value;
    const query = byId("filterSearch").value.toLowerCase();
    const rows = allIssues.filter((issue) => {
      const searchable = `${issue.message || ""} ${issue.rule_id || ""} ${issue.recommendation || ""}`.toLowerCase();
      const severityMatches = explicitSeverities.length
        ? explicitSeverities.includes(issue.severity)
        : !severity || issue.severity === severity;
      return severityMatches
        && (!sheet || issue.sheet_name === sheet)
        && (!ruleType || issue.rule_type === ruleType)
        && (!query || searchable.includes(query));
    });

    issueBody.replaceChildren();
    if (!rows.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 8;
      cell.textContent = "No issues match the current filters.";
      row.appendChild(cell);
      issueBody.appendChild(row);
      return;
    }

    const fragment = document.createDocumentFragment();
    rows.slice(0, 500).forEach((issue) => {
      const row = document.createElement("tr");
      appendTextCell(row, issue.severity);
      appendTextCell(row, issue.sheet_name);
      appendTextCell(row, issue.row_number || "");
      appendTextCell(row, issue.column_name || "");
      appendTextCell(row, issue.rule_id, true);
      appendTextCell(row, issue.message);
      appendTextCell(row, issue.actual_value || "");
      appendTextCell(row, issue.recommendation || "");
      fragment.appendChild(row);
    });
    issueBody.appendChild(fragment);
  }

  function populateSelect(id, label, values) {
    const select = byId(id);
    select.replaceChildren();
    const initial = document.createElement("option");
    initial.value = "";
    initial.textContent = label;
    select.appendChild(initial);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function renderSheetTabs(sheets) {
    const container = byId("cobieQaSheetTabs");
    container.replaceChildren();
    sheets.forEach((sheet) => {
      const button = document.createElement("button");
      button.className = "btn secondary";
      button.type = "button";
      button.textContent = sheet;
      button.addEventListener("click", () => {
        byId("filterSheet").value = sheet;
        renderIssues();
      });
      container.appendChild(button);
    });
  }

  ["filterSeverity", "filterSheet", "filterRuleType", "filterSearch", "filterErrorsOnly", "filterWarningsOnly"]
    .forEach((id) => byId(id).addEventListener("input", renderIssues));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = byId("cobieQaFile").files[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      window.alert("Only .xlsx files are supported.");
      return;
    }

    const body = new FormData();
    body.append("file", file);
    body.append("rule_pack", byId("cobieQaRulePack").value);
    progress(0.12, "Uploading", "Uploading temporary session file...");
    byId("cobieQaRun").disabled = true;

    try {
      const validationResponse = await fetch("/api/cobie/validate", { method: "POST", body });
      if (!validationResponse.ok) {
        const failure = await validationResponse.json().catch(() => ({ detail: validationResponse.statusText }));
        throw new Error(failure.detail || "Validation failed");
      }
      const validation = await validationResponse.json();
      jobId = validation.job_id;
      progress(1, "Complete", "Validation complete. Building dashboard...");
      byId("cobieQaResults").style.display = "block";

      const jobResponse = await fetch(`/api/cobie/jobs/${jobId}`);
      if (!jobResponse.ok) throw new Error("The validation result is no longer available.");
      const job = await jobResponse.json();
      const summary = job.summary;
      byId("cobieQaOverall").textContent = summary.overall_status;
      byId("qaErrors").textContent = summary.errors;
      byId("qaWarnings").textContent = summary.warnings;
      byId("qaInfo").textContent = summary.info;
      byId("qaSheets").textContent = summary.sheets_checked;
      byId("qaRows").textContent = summary.rows_checked;

      const issuesResponse = await fetch(`/api/cobie/jobs/${jobId}/issues?limit=5000`);
      if (!issuesResponse.ok) throw new Error("The validation issues could not be loaded.");
      const issuesPayload = await issuesResponse.json();
      allIssues = issuesPayload.issues;
      const sheets = [...new Set(allIssues.map((issue) => issue.sheet_name).filter(Boolean))].sort();
      const ruleTypes = [...new Set(allIssues.map((issue) => issue.rule_type).filter(Boolean))].sort();
      populateSelect("filterSheet", "All worksheets", sheets);
      populateSelect("filterRuleType", "All rule types", ruleTypes);
      renderSheetTabs(sheets);

      byId("downloadReport").href = `/api/cobie/jobs/${jobId}/report.xlsx`;
      byId("downloadCsv").href = `/api/cobie/jobs/${jobId}/issues.csv`;
      byId("downloadMarked").href = `/api/cobie/jobs/${jobId}/marked-up.xlsx`;
      renderIssues();
    } catch (error) {
      progress(1, "Failed", error instanceof Error ? error.message : "Validation failed");
    } finally {
      byId("cobieQaRun").disabled = false;
    }
  });
})();
