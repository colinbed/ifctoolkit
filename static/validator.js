(() => {
  const button = document.querySelector("#uploadBtn");
  const input = document.querySelector("#fileInput");
  const status = document.querySelector("#status");

  button?.addEventListener("click", async () => {
    const file = input.files[0];
    if (!file) {
      status.textContent = "Choose an IFC, IFCZIP, or XLSX file.";
      return;
    }
    if (!/\.(ifc|ifczip|xlsx)$/i.test(file.name)) {
      status.textContent = "Unsupported file type.";
      return;
    }

    status.textContent = "Creating secure temporary session…";
    const session = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then((response) => response.json());

    const form = new FormData();
    form.append("files", file);
    status.textContent = "Uploading for temporary processing…";
    const response = await fetch(`/api/session/${session.session_id}/upload`, {
      method: "POST",
      body: form,
    });
    status.textContent = response.ok
      ? "Upload complete. Validation workflow is ready to process this file."
      : "Upload failed safely. Please check the file and try again.";
  });
})();
