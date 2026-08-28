(() => {
  "use strict";
  const root = document.getElementById("ifc-uploader");
  if (!root) return;

  const input = document.getElementById("ifc-file");
  const dropZone = document.getElementById("ifc-drop-zone");
  const status = document.getElementById("ifc-upload-status");
  const next = document.getElementById("ifc-next");
  const maxBytes = Number(root.dataset.maxBytes);
  const projectId = root.dataset.projectId;
  let selectedFile = null;
  let activeRequest = null;
  let dragDepth = 0;
  let replacement = {};
  let currentUpload = null;

  const formatSize = bytes => `${(bytes / 1048576).toFixed(1)} MB`;
  const escapeHtml = value => String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
  const details = file => `<strong class="upload-filename">${escapeHtml(file.name)}</strong><span>${formatSize(file.size)}</span>`;

  function render(state, message = "") {
    status.className = `upload-state ${state.toLowerCase()}`;
    if (state === "SELECTED") {
      status.innerHTML = `<p><strong>IFC selected</strong></p>${details(selectedFile)}<div class="upload-actions"><button type="button" class="link remove-selection">Remove</button><button type="button" class="link replace-selection">Replace</button></div>`;
    } else if (state === "UPLOADING") {
      status.innerHTML = `<p><strong>Uploading IFC...</strong></p><div class="upload-progress"><i></i></div><span class="upload-percent" aria-label="Upload progress">Starting…</span>${details(selectedFile)}<div class="upload-actions"><button type="button" class="link remove-selection">Cancel</button></div>`;
    } else if (state === "UPLOADED") {
      status.innerHTML = `<p><strong>✓ IFC uploaded</strong></p>${details(selectedFile)}<span>Status: Ready for model scan</span><div class="upload-actions"><button type="button" class="link replace-selection">Replace IFC</button><button type="button" class="link danger remove-uploaded">Remove</button></div>`;
    } else if (state === "FAILED") {
      status.innerHTML = `<p><strong>Upload failed</strong></p><span>${escapeHtml(message || "We couldn't upload the IFC file.")}</span>${selectedFile ? details(selectedFile) : ""}<div class="upload-actions"><button type="button" class="button small retry-upload">Retry</button><button type="button" class="link replace-selection">Choose another file</button></div>`;
    } else status.replaceChildren();
  }

  function validate(fileList) {
    if (!fileList || fileList.length !== 1) return "Choose exactly one IFC file.";
    const file = fileList[0];
    if (!file.name.toLowerCase().endsWith(".ifc")) return "Select an IFC file with the .ifc extension.";
    if (!file.size) return "The selected IFC file is empty.";
    if (file.size > maxBytes) return "The IFC file exceeds the 500 MB upload limit.";
    return "";
  }

  async function jsonRequest(url, options) {
    const response = await fetch(url, {...options, headers: {"Content-Type": "application/json"}});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "We couldn't upload the IFC file.");
    return body;
  }

  async function upload(file) {
    render("UPLOADING");
    let prepared = null;
    try {
      prepared = await jsonRequest(`/app/projects/${encodeURIComponent(projectId)}/regulation-38/ifc/initiate`, {
        method: "POST", body: JSON.stringify({filename: file.name, file_size: file.size})
      });
      await new Promise((resolve, reject) => {
        const xhr = activeRequest = new XMLHttpRequest();
        xhr.open("PUT", prepared.signed_url);
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        xhr.upload.addEventListener("progress", event => {
          if (!event.lengthComputable) return;
          const percent = Math.round(event.loaded / event.total * 100);
          status.querySelector(".upload-progress i").style.width = `${percent}%`;
          status.querySelector(".upload-percent").textContent = `${percent}%`;
        });
        xhr.addEventListener("load", () => { if (xhr.status >= 200 && xhr.status < 300) resolve(); else { const error = new Error("We couldn't upload the IFC file."); error.storageStatus = xhr.status; reject(error); } });
        xhr.addEventListener("error", () => reject(new Error("We couldn't upload the IFC file.")));
        xhr.addEventListener("abort", () => reject(new Error("Upload cancelled.")));
        xhr.send(file);
      });
      await jsonRequest(`/app/projects/${encodeURIComponent(projectId)}/regulation-38/ifc/finalize`, {
        method: "POST", body: JSON.stringify({...prepared, ...replacement, filename: file.name, file_size: file.size})
      });
      activeRequest = null;
      currentUpload = prepared;
      render("UPLOADED");
      next.classList.remove("disabled");
      next.removeAttribute("aria-disabled");
      next.removeAttribute("tabindex");
      next.href = `/app/regulation-38/projects/${encodeURIComponent(projectId)}/setup/model-scan`;
    } catch (error) {
      activeRequest = null;
      if (prepared) fetch(`/app/projects/${encodeURIComponent(projectId)}/regulation-38/ifc/failure`, {
        method: "POST", headers: {"Content-Type": "application/json"}, keepalive: true,
        body: JSON.stringify({filename: file.name, file_size: file.size, storage_path: prepared.storage_path, storage_http_status: error.storageStatus || 0})
      }).catch(() => {});
      render("FAILED", error.message);
    }
  }

  function selectFiles(files) {
    const problem = validate(files);
    if (problem) { selectedFile = null; render("FAILED", problem); input.value = ""; return; }
    selectedFile = files[0];
    render("SELECTED");
    window.setTimeout(() => upload(selectedFile), 0);
  }

  input.addEventListener("change", () => selectFiles(input.files));
  dropZone.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
  });
  ["dragenter", "dragover", "dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => {
    event.preventDefault(); event.stopPropagation();
  }));
  dropZone.addEventListener("dragenter", () => { dragDepth += 1; dropZone.classList.add("drag-over"); });
  dropZone.addEventListener("dragover", event => { event.dataTransfer.dropEffect = "copy"; });
  dropZone.addEventListener("dragleave", () => { dragDepth -= 1; if (dragDepth <= 0) { dragDepth = 0; dropZone.classList.remove("drag-over"); } });
  dropZone.addEventListener("drop", event => { dragDepth = 0; dropZone.classList.remove("drag-over"); selectFiles(event.dataTransfer.files); });
  status.addEventListener("click", event => {
    if (event.target.closest(".replace-selection")) input.click();
    if (event.target.closest(".retry-upload") && selectedFile) upload(selectedFile);
    if (event.target.closest(".remove-selection")) { if (activeRequest) activeRequest.abort(); selectedFile = null; input.value = ""; render("EMPTY"); }
    if (event.target.closest(".remove-uploaded") && currentUpload) {
      const body = new FormData(); body.append("storage_path", currentUpload.storage_path);
      fetch(`/app/projects/${encodeURIComponent(projectId)}/regulation-38/ifc/${encodeURIComponent(currentUpload.file_id)}/remove`, {method:"POST", body})
        .then(response => { if (!response.ok) throw new Error(); selectedFile = null; currentUpload = null; input.value = ""; render("EMPTY"); next.classList.add("disabled"); next.removeAttribute("href"); next.setAttribute("aria-disabled", "true"); next.setAttribute("tabindex", "-1"); })
        .catch(() => render("FAILED", "We couldn't remove the IFC file."));
    }
  });
  document.querySelectorAll(".replace-ifc").forEach(button => button.addEventListener("click", () => {
    const article = button.closest("article");
    replacement = {replace_file_id: article.dataset.fileId, replace_storage_path: article.querySelector('input[name="storage_path"]').value};
    input.click();
  }));
})();
