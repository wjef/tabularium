// Export form: POST with the CSRF header (a plain form cannot send it) and save the zip.
(function () {
  "use strict";

  function csrfHeaders() {
    try {
      return JSON.parse(document.body.getAttribute("hx-headers") || "{}");
    } catch (e) {
      return {};
    }
  }

  function filenameFrom(response) {
    const header = response.headers.get("Content-Disposition") || "";
    const match = /filename="([^"]+)"/.exec(header);
    return match ? match[1] : "Tabularium export.zip";
  }

  document.addEventListener("change", function (event) {
    const all = event.target.closest("[data-select-all]");
    if (!all) return;
    all.form.querySelectorAll('input[name="doc_ids"]').forEach(function (box) {
      box.checked = all.checked;
    });
  });

  document.addEventListener("submit", async function (event) {
    const form = event.target.closest("form[data-export]");
    if (!form) return;
    event.preventDefault();
    const status = form.querySelector(".export-status");
    const data = new FormData(form);
    if (!data.getAll("doc_ids").length) {
      status.textContent = form.dataset.msgEmpty;
      return;
    }
    status.textContent = form.dataset.msgBusy;
    const response = await fetch(form.action, { method: "POST", body: data, headers: csrfHeaders() });
    if (!response.ok) {
      status.textContent = form.dataset.msgFailed + " " + (await response.text());
      return;
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = filenameFrom(response);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    status.textContent = "";
  });
})();
