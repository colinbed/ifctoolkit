(() => {
  const data = JSON.parse(document.getElementById("spatial-data").textContent);
  const config = window.reg38SpatialConfig;
  const byId = (rows) => Object.fromEntries(rows.map((row) => [row.id, row]));
  const spaces = byId(data.spaces), zones = byId(data.zones);
  const members = data.members.reduce((all, member) => ((all[member.zone_id] ||= []).push(member), all), {});
  const tree = document.getElementById("structure-tree"), details = document.getElementById("spatial-details");
  const svg = document.getElementById("storey-plan");
  let selectedStorey = null, selectedSpace = null, planRows = [], planRequest = 0;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const source = (space) => space.ifc_objects || {};
  const storey = (space) => space.building_storeys || {id: space.storey_id, name: "Unknown storey"};
  const changed = (working, original) => String(working ?? "") !== String(original ?? "") ? '<small class="changed">Working value changed from IFC</small>' : "";
  const fireZones = Object.values(zones).filter((z) => z.zone_type === "FIRE_COMPARTMENT");
  const spaceZoneIds = (id) => data.members.filter((m) => m.space_id === id).map((m) => m.zone_id);
  const warningFor = (space) => {
    const warnings = [];
    if (!source(space).name && !source(space).long_name && !space.space_number) warnings.push("Unnamed space");
    const dupName = Object.values(spaces).filter((x) => x.id !== space.id && ((space.name && x.name === space.name) || (space.space_number && x.space_number === space.space_number)));
    if (dupName.length) warnings.push("Duplicate name or number");
    if (fireZones.length && !spaceZoneIds(space.id).some((id) => zones[id]?.zone_type === "FIRE_COMPARTMENT")) warnings.push("Not assigned to a fire compartment");
    return warnings;
  };
  const warnings = Object.values(spaces).flatMap(warningFor).length + Object.values(zones).filter((z) => !(members[z.id] || []).length).length;
  document.getElementById("warning-count").textContent = `${warnings} review warning${warnings === 1 ? "" : "s"}`;

  function field(label, name, value, type = "text") {
    return `<label>${label}<input type="${type}" name="${name}" value="${esc(value)}" ${config.canAdmin ? "" : "disabled"}></label>`;
  }
  function sourceRow(label, value) { return `<div class="source-row"><span>${label}</span><strong>${esc(value || "—")}</strong></div>`; }
  function memberChecks(selected) {
    return `<div class="member-list">${Object.values(spaces).map((space) => `<label><input type="checkbox" name="space_ids" value="${space.id}" ${selected.has(space.id) ? "checked" : ""} ${config.canAdmin ? "" : "disabled"}><span>${esc(space.space_number || "—")} · ${esc(space.name)}</span>${selected.has(space.id) && selected.get(space.id) !== "MANUAL" ? '<em>IFC-derived</em>' : ""}</label>`).join("")}</div>`;
  }
  function selectSpace(id) {
    const space = spaces[id], original = source(space), ws = warningFor(space);
    selectedStorey = space.storey_id; selectedSpace = space.id; loadPlan(); renderTree(document.getElementById("spatial-search").value);
    details.innerHTML = `<form method="post" action="/app/projects/${config.projectId}/regulation-38/spaces/${id}">
      <div class="detail-title"><div><span class="source-badge">Source: IFC</span><h3>${esc(space.name)}</h3></div>${ws.length ? `<span class="warning-dot" title="${esc(ws.join('; '))}">${ws.length}</span>` : ""}</div>
      <h4>Source information <small>Read-only</small></h4>${sourceRow("IFC GlobalId", space.ifc_global_id)}${sourceRow("IFC entity", original.ifc_entity || "IfcSpace")}${sourceRow("Original Name", original.name)}${sourceRow("Original LongName", original.long_name)}${sourceRow("Original description", original.description)}${sourceRow("Source storey", storey(space).name)}${sourceRow("Net / gross area", `${space.net_area ?? "—"} / ${space.gross_area ?? "—"}`)}${sourceRow("Geometry", space.source_geometry?.type === "Polygon" ? `${space.source_geometry.geometry_method || "IFC"} · ${space.source_geometry.confidence || "—"} confidence` : "Unavailable")}
      <h4>Working fields</h4>${field("Space number", "space_number", space.space_number)}${changed(space.space_number, original.name)}${field("Name", "name", space.name)}${changed(space.name, original.long_name)}<label>Description<textarea name="description" ${config.canAdmin ? "" : "disabled"}>${esc(space.description)}</textarea></label>${changed(space.description, original.description)}${field("Occupancy type", "occupancy_type", space.occupancy_type)}${field("Occupancy capacity", "occupancy_capacity", space.occupancy_capacity, "number")}
      <label>High-risk?<select name="high_risk" ${config.canAdmin ? "" : "disabled"}><option value="no" ${!space.high_risk ? "selected" : ""}>No</option><option value="yes" ${space.high_risk ? "selected" : ""}>Yes</option></select></label><label>Include in Regulation 38?<select name="included_in_reg38" ${config.canAdmin ? "" : "disabled"}><option value="yes" ${space.included_in_reg38 ? "selected" : ""}>Yes</option><option value="no" ${!space.included_in_reg38 ? "selected" : ""}>No</option></select></label>
      ${ws.map((w) => `<p class="review-warning">${esc(w)}</p>`).join("")}${config.canAdmin ? '<button class="button small">Save working values</button>' : ""}</form>`;
  }
  function selectZone(id) {
    const zone = zones[id], selected = new Map((members[id] || []).map((m) => [m.space_id, m.source]));
    if (zone.storey_id) { selectedStorey = zone.storey_id; selectedSpace = null; loadPlan(); }
    const fire = zone.source_kind === "IFC_SPATIAL_ZONE" && String(zone.source_predefined_type).toUpperCase() === "FIRESAFETY";
    details.innerHTML = `<form method="post" action="/app/projects/${config.projectId}/regulation-38/zones/${id}"><div class="detail-title"><div><span class="source-badge">Source: ${zone.source_kind === "MANUAL" ? "Manual" : "IFC"}</span><h3>${esc(zone.name)}</h3></div></div>${fire ? '<p class="fire-zone">Fire-safety spatial zone · admin review required</p>' : ""}${field("Working zone name", "name", zone.name)}<label>Zone type<select name="zone_type" ${config.canAdmin ? "" : "disabled"}>${config.zoneTypes.map((type) => `<option ${type === zone.zone_type ? "selected" : ""}>${type}</option>`).join("")}</select></label><h4>Spaces <small>${selected.size} members</small></h4>${memberChecks(selected)}${!selected.size ? '<p class="review-warning">Zone has no members</p>' : ""}${config.canAdmin ? '<button class="button small">Save zone</button>' : ""}</form>`;
  }
  function newZone() {
    details.innerHTML = `<form method="post" action="/app/projects/${config.projectId}/regulation-38/zones"><div class="detail-title"><h3>Create manual zone</h3></div>${field("Zone name", "name", "")}<label>Zone type<select name="zone_type">${config.zoneTypes.map((type) => `<option ${type === "USER_DEFINED" ? "selected" : ""}>${type}</option>`).join("")}</select></label><h4>Select spaces</h4>${memberChecks(new Map())}<button class="button small">Create zone</button></form>`;
  }
  function renderTree(query = "") {
    const q = query.trim().toLowerCase(), grouped = {};
    Object.values(spaces).filter((s) => !q || `${s.space_number || ""} ${s.name || ""}`.toLowerCase().includes(q)).forEach((s) => ((grouped[s.storey_id] ||= {storey: storey(s), spaces: []}).spaces.push(s)));
    tree.innerHTML = `<h4>Storeys</h4>${Object.values(grouped).map((g) => `<section><button class="tree-storey" data-storey="${g.storey.id}"><span>⌄ ${esc(g.storey.name)}</span><b>${g.spaces.length} spaces</b></button>${g.spaces.map((s) => `<button data-space="${s.id}" class="${s.id === selectedSpace ? "selected" : ""}" aria-pressed="${s.id === selectedSpace}"><span>${esc(s.space_number || s.name || "—")}${s.space_number && s.name ? ` — ${esc(s.name)}` : ""}</span>${warningFor(s).length ? `<i>${warningFor(s).length}</i>` : ""}</button>`).join("")}</section>`).join("")}<h4>Zones</h4>${Object.values(zones).filter((z) => !q || z.name.toLowerCase().includes(q)).map((z) => `<button data-zone="${z.id}" class="${z.source_kind === 'IFC_SPATIAL_ZONE' && String(z.source_predefined_type).toUpperCase() === 'FIRESAFETY' ? 'fire-safety' : ''}"><span>${esc(z.name)}</span><b>${(members[z.id] || []).length}</b></button>`).join("")}`;
    tree.querySelectorAll("[data-space]").forEach((el) => el.onclick = () => selectSpace(el.dataset.space));
    tree.querySelectorAll("[data-zone]").forEach((el) => el.onclick = () => selectZone(el.dataset.zone));
    tree.querySelectorAll("[data-storey]").forEach((el) => el.onclick = () => { selectedStorey = el.dataset.storey; selectedSpace = null; loadPlan(); });
  }
  const planViewer = new window.FiretracePlanViewer(svg, {
    gridToggle: document.getElementById("grid-toggle"),
    onSelect: (space) => selectSpace(space.id),
  });
  const geometry = (space) => planViewer.geometry(space);
  const emptyMessage = () => {
    const unavailable = selectedSpace && spaces[selectedSpace]?.source_geometry;
    const reason = unavailable?.reason;
    if (reason === "NO_REPRESENTATION") return "Space geometry is not explicitly represented in the IFC. FireTrace detected this IfcSpace, but the source model provides no direct representation or usable spatial boundary.";
    if (reason === "NO_BOUNDARY_DATA") return "The IFC identifies this space and its boundaries, but does not provide usable ConnectionGeometry. Review manually, create working geometry, or replace the Design Model.";
    if (reason) return "Space detected, but FireTrace could not derive a closed plan footprint from its IFC geometry.";
    return "Plan geometry has not been generated. Re-run Model Scan to backfill this space.";
  };
  function drawPlan(fitTarget = null) {
    if (!planRows.some((row) => geometry(row).length)) {
      svg.setAttribute("viewBox", "0 0 800 560");
      svg.innerHTML = `<g class="preview-empty"><text x="400" y="260" text-anchor="middle" font-weight="700">Preview unavailable</text><text x="400" y="290" text-anchor="middle">${esc(emptyMessage())}</text></g>`;
      return;
    }
    if (fitTarget === "storey" || !planViewer.viewBox) planViewer.setRows(planRows, selectedSpace);
    else { planViewer.rows=planRows;planViewer.selectedId=selectedSpace;fitTarget === "room" ? planViewer.fit("selection") : planViewer.render(); }
  }
  async function loadPlan() {
    const request = ++planRequest;
    document.getElementById("plan-title").textContent = Object.values(spaces).find((s) => s.storey_id === selectedStorey) ? storey(Object.values(spaces).find((s) => s.storey_id === selectedStorey)).name : "Selected storey";
    svg.innerHTML = '<text x="400" y="280" text-anchor="middle">Loading stored plan geometry…</text>';
    try {
      const response = await fetch(`/api/firetrace/projects/${config.projectId}/spatial/storeys/${selectedStorey}`, {headers:{Accept:"application/json"}});
      if (!response.ok) throw new Error("Plan unavailable");
      const payload = await response.json(); if (request !== planRequest) return;
      planRows = payload.spaces || []; planViewer.viewBox = null; drawPlan(selectedSpace ? "room" : "storey");
    } catch (_) {
      if (request === planRequest) { planRows = []; drawPlan(); }
    }
  }
  document.querySelectorAll("[data-plan-action]").forEach((button) => button.onclick = () => {
    const action = button.dataset.planAction;
    if (action === "room") return planViewer.fit("selection");
    if (action === "storey") return planViewer.fit("storey");
    if (action === "reset") return planViewer.reset();
    planViewer.zoom(action === "in" ? .8 : 1.25);
  });
  renderTree();
  document.getElementById("spatial-search").oninput = (event) => renderTree(event.target.value);
  document.getElementById("new-zone")?.addEventListener("click", newZone);
  const requested = new URLSearchParams(location.search).get("selected"); if (requested?.startsWith("space:")) selectSpace(requested.slice(6)); else if (requested?.startsWith("zone:")) selectZone(requested.slice(5));
})();
