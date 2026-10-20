(() => {
  const $ = (id) => document.getElementById(id);
  if (!$('fa-scan')) return;
  let scan = null;
  const sessionId = () => localStorage.getItem('ifc_session_id');
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function refreshFiles(selectName) {
    const response = await fetch(`/api/session/${sessionId()}/files?page=flatten-assemblies`);
    const data = await response.json();
    const files = (data.files || []).filter((f) => f.name.toLowerCase().endsWith('.ifc'));
    $('fa-source').innerHTML = '<option value="">Choose a file</option>' + files.map((f) => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('');
    if (selectName) $('fa-source').value = selectName;
  }
  async function upload() {
    const file = $('fa-upload').files[0];
    if (!file) return show('Choose an IFC file first.');
    const form = new FormData(); form.append('files', file);
    const response = await window.withProcessing('Uploading IFC…', () => fetch(`/api/session/${sessionId()}/upload`, {method:'POST', body:form}));
    const data = await response.json();
    if (!response.ok) return show(data.detail || 'Upload failed.');
    await refreshFiles(file.name); await doScan();
  }
  const show = (message) => { $('fa-results').hidden = false; $('fa-status').textContent = message; };
  async function requestScan(target) {
    const response = await fetch('/api/ifc-tools/flatten-assemblies/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:sessionId(), source_file:$('fa-source').value, target_entity:target || undefined})});
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Scan failed'); return data.scan;
  }
  async function doScan() {
    if (!$('fa-source').value) return show('Choose an IFC file first.');
    try {
      scan = await window.withProcessing('Scanning IFC decomposition relationships…', () => requestScan());
      $('fa-target-options').innerHTML = scan.target_entities.map((x) => `<option value="${esc(x.ifc_class)}">${esc(x.ifc_class)} (${x.count})</option>`).join('');
      $('fa-target').value = scan.default_target_entity || ''; $('fa-config').hidden = false;
      show(`Found ${scan.assemblies.length} assemblies in ${scan.schema}. Choose a retained entity class.`);
    } catch (error) { show(error.message); }
  }
  async function preview() {
    try { scan = await requestScan($('fa-target').value.trim()); renderAssemblies(); }
    catch (error) { show(error.message); }
  }
  function renderAssemblies() {
    $('fa-preview-card').hidden = false; $('fa-counts').textContent = `${scan.assemblies.length} assemblies`;
    $('fa-assemblies').innerHTML = scan.assemblies.map((a) => {
      const options = a.matching_components.map((c) => `<option value="${esc(c.guid)}" ${a.proposed_survivor?.guid === c.guid ? 'selected' : ''}>${esc(c.name)} · ${esc(c.guid)}</option>`).join('');
      const choice = a.status === 'choice_required' ? `<option value="" selected>Choose surviving object…</option>${options}` : options || '<option value="">No matching component</option>';
      const conflict = a.overlaps.length ? ` · overlaps ${a.overlaps.map(esc).join(', ')}` : '';
      return `<tr data-assembly="${esc(a.guid)}"><td><input class="fa-include" type="checkbox" ${a.status !== 'skipped' ? 'checked' : ''} ${a.status === 'skipped' ? 'disabled' : ''} aria-label="Include ${esc(a.name)}"></td><td><strong>${esc(a.name)}</strong><br><small>${esc(a.guid)}${conflict}</small></td><td><select class="fa-survivor">${choice}</select><br><small>${esc($('fa-target').value)}</small></td><td>${a.absorbed_count}</td><td>${esc(a.status)} ${esc(a.reason)}</td></tr>`;
    }).join('');
  }
  async function run() {
    const assemblies = [...document.querySelectorAll('#fa-assemblies tr')].map((row) => ({assembly_guid:row.dataset.assembly, include:row.querySelector('.fa-include').checked, survivor_guid:row.querySelector('.fa-survivor').value || null}));
    const response = await fetch('/api/ifc-tools/flatten-assemblies/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:sessionId(), source_file:$('fa-source').value, target_entity:$('fa-target').value.trim(), assemblies})});
    const data = await response.json(); if (!response.ok) return show(data.detail || 'Could not start flattening.');
    show('Flattening queued…'); poll(data.job_id);
  }
  async function poll(jobId) {
    const response = await fetch(`/api/ifc-tools/flatten-assemblies/jobs/${jobId}`); const job = await response.json();
    $('fa-progress').value = job.progress || 0; $('fa-status').textContent = job.message || job.status;
    if (job.status === 'complete') {
      $('fa-downloads').innerHTML = job.outputs.map((x) => `<div class="file-pill"><span>${esc(x.name)}</span><a class="ghost" href="/api/session/${sessionId()}/download?name=${encodeURIComponent(x.name)}">Download ${esc(x.kind)}</a></div>`).join('');
      $('fa-report').textContent = JSON.stringify(job.result, null, 2); await refreshFiles();
    } else if (job.status !== 'failed') setTimeout(() => poll(jobId), 750);
  }
  $('fa-upload-button').addEventListener('click', upload); $('fa-scan').addEventListener('click', doScan); $('fa-preview').addEventListener('click', preview); $('fa-run').addEventListener('click', run);
  refreshFiles().catch((e) => show(e.message));
})();
