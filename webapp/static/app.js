const STATUS_LABELS = {
  new: "New",
  scored: "Scored",
  queued_for_digest: "Queued for digest",
  sent_in_digest: "Sent in digest",
  applied: "Applied",
  rejected: "Rejected",
  excluded: "Excluded",
};

async function api(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status}: ${body}`);
  }
  return resp.status === 204 ? null : resp.json();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

function timeAgo(isoString) {
  if (!isoString) return "";
  const diffMs = Date.now() - new Date(isoString).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

async function loadStats() {
  const stats = await api("/api/stats");
  const order = ["queued_for_digest", "sent_in_digest", "applied", "scored", "new", "rejected", "excluded"];
  const parts = order
    .filter((k) => stats[k])
    .map((k) => `<span><strong>${stats[k]}</strong> ${STATUS_LABELS[k]}</span>`);
  document.getElementById("stats-bar").innerHTML = parts.join("") || "No jobs yet.";
}

async function loadRuns() {
  const runs = await api("/api/runs?limit=10");
  const el = document.getElementById("runs-list");
  if (!runs.length) {
    el.innerHTML = '<div class="empty">No pipeline runs recorded yet.</div>';
    return;
  }
  el.innerHTML = runs
    .map(
      (r) => `
    <div class="run-row">
      <span class="run-type">${r.run_type}</span>
      <span>${timeAgo(r.started_at)}</span>
      <span>found ${r.jobs_found ?? 0}, new ${r.jobs_new ?? 0}</span>
      ${r.error ? `<span class="run-error">${escapeHtml(r.error)}</span>` : "<span>ok</span>"}
    </div>`
    )
    .join("");
}

function jobCard(job) {
  const score = job.fit_score != null ? Math.round(job.fit_score) : "—";
  const statusClass = `status-${job.status}`;
  const draft = job.outreach_draft || "";
  return `
  <div class="job-card" data-id="${job.id}">
    <div class="job-card-top">
      <span class="job-score">Fit: <strong>${score}</strong>/100 &middot; ${escapeHtml(job.source)} &middot; ${timeAgo(job.discovered_at)}</span>
      <span class="status-badge ${statusClass}">${STATUS_LABELS[job.status] || job.status}</span>
    </div>
    <div class="job-title"><a href="${job.url}" target="_blank" rel="noopener">${escapeHtml(job.title)}</a></div>
    <div class="job-meta">${escapeHtml(job.company)} &middot; ${escapeHtml(job.location || "location not stated")}</div>
    ${job.fit_rationale ? `<div class="job-rationale">${escapeHtml(job.fit_rationale)}</div>` : ""}
    ${
      draft
        ? `<div class="outreach-box"><textarea data-draft-for="${job.id}">${escapeHtml(draft)}</textarea></div>`
        : ""
    }
    <div class="job-actions">
      ${draft ? `<button data-action="save-draft" data-id="${job.id}">Save edits</button>` : ""}
      ${draft ? `<button data-action="copy-draft" data-id="${job.id}">Copy draft</button>` : ""}
      <button data-action="applied" data-id="${job.id}">Mark applied</button>
      <button data-action="rejected" data-id="${job.id}" class="danger">Reject</button>
    </div>
  </div>`;
}

async function loadJobs() {
  const status = document.getElementById("filter-status").value;
  const q = document.getElementById("filter-search").value.trim();
  const minScore = document.getElementById("filter-min-score").value;

  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (q) params.set("q", q);
  if (minScore) params.set("min_score", minScore);

  const jobs = await api(`/api/jobs?${params.toString()}`);
  const el = document.getElementById("jobs-list");
  el.innerHTML = jobs.length ? jobs.map(jobCard).join("") : '<div class="empty">No jobs match this filter.</div>';
}

async function updateJob(id, fields) {
  await api(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
}

document.getElementById("jobs-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.dataset.id;
  const action = btn.dataset.action;

  try {
    if (action === "applied") {
      await updateJob(id, { status: "applied" });
      await loadJobs();
      await loadStats();
    } else if (action === "rejected") {
      await updateJob(id, { status: "rejected" });
      await loadJobs();
      await loadStats();
    } else if (action === "save-draft") {
      const textarea = document.querySelector(`textarea[data-draft-for="${id}"]`);
      await updateJob(id, { outreach_draft: textarea.value });
      btn.textContent = "Saved ✓";
      setTimeout(() => (btn.textContent = "Save edits"), 1500);
    } else if (action === "copy-draft") {
      const textarea = document.querySelector(`textarea[data-draft-for="${id}"]`);
      await navigator.clipboard.writeText(textarea.value);
      btn.textContent = "Copied ✓";
      setTimeout(() => (btn.textContent = "Copy draft"), 1500);
    }
  } catch (err) {
    alert(`Action failed: ${err.message}`);
  }
});

document.getElementById("filter-apply").addEventListener("click", loadJobs);
document.getElementById("filter-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadJobs();
});

(async function init() {
  try {
    await Promise.all([loadStats(), loadRuns(), loadJobs()]);
  } catch (err) {
    document.body.innerHTML = `<p style="color:#f05252;padding:40px;">Failed to load: ${err.message}</p>`;
  }
})();
