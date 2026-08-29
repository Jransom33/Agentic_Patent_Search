const MAX_UPLOAD_BYTES = 5_000_000;
const POLL_MS = 2000;

const form = document.getElementById("job-form");
const specInput = document.getElementById("specification");
const claimsInput = document.getElementById("claims");
const dateInput = document.getElementById("critical-date");
const specLabel = document.getElementById("spec-label");
const claimsLabel = document.getElementById("claims-label");
const statusEl = document.getElementById("status");
const submitBtn = document.getElementById("submit");
const reportEl = document.getElementById("report");

let pollId = 0;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function safeHref(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch {
    /* ignore malformed provider URLs */
  }
  return "";
}

function setStatus(text, isError = false) {
  statusEl.hidden = !text;
  statusEl.textContent = text;
  statusEl.classList.toggle("is-error", isError);
}

function fileName(input, fallback) {
  return input.files[0]?.name || fallback;
}

specInput.addEventListener("change", () => {
  specLabel.textContent = fileName(specInput, "Specification");
});
claimsInput.addEventListener("change", () => {
  claimsLabel.textContent = fileName(claimsInput, "Claims");
});

function detailMessage(body) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && detail.error_code) {
    return detail.error_code.replaceAll("_", " ");
  }
  return "Request failed";
}

async function poll(jobId, token) {
  const response = await fetch(`/jobs/${encodeURIComponent(jobId)}`);
  const body = await response.json().catch(() => ({}));
  if (token !== pollId) return;

  if (!response.ok) {
    setStatus(detailMessage(body), true);
    submitBtn.disabled = false;
    return;
  }

  const status = body.status || "unknown";
  if (status === "completed") {
    setStatus(`Completed · ${jobId}`);
    renderReport(body.report);
    submitBtn.disabled = false;
    return;
  }
  if (status === "failed") {
    const code = body.error_code ? ` · ${body.error_code.replaceAll("_", " ")}` : "";
    setStatus(`Failed${code}`, true);
    submitBtn.disabled = false;
    return;
  }

  const label = status.charAt(0).toUpperCase() + status.slice(1);
  setStatus(`${label}… · ${jobId}`);
  window.setTimeout(() => {
    poll(jobId, token).catch(() => setStatus("Lost connection while polling", true));
  }, POLL_MS);
}

function quoted(text) {
  return `&ldquo;${esc(text)}&rdquo;`;
}

// Wraps one labelled block of a card, or nothing when there is no content.
function field(label, body) {
  return body ? `<section class="field"><p class="label">${label}</p>${body}</section>` : "";
}

function renderReport(report) {
  if (!report) {
    reportEl.innerHTML = `<p class="report-meta">Job completed, but no report was stored.</p>`;
    document.getElementById("findings").scrollIntoView({ behavior: "smooth" });
    return;
  }

  const notes = (report.uncertainty_notes || [])
    .map((note) => `<li>${esc(note)}</li>`)
    .join("");
  const cards = (report.evidence || []).map((item) => {
    const cand = item.candidate || {};
    const href = safeHref(cand.url);
    const title = esc(cand.title || "Untitled");
    const heading = href
      ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${title}</a>`
      : title;
    const source = href
      ? `<a class="source-link" href="${esc(href)}" target="_blank" rel="noopener noreferrer" aria-label="Open source">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4M14 4h6v6M10 14L20 4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </a>`
      : "";
    const cites = (item.citations || [])
      .map((cite) => `<p class="cite">${quoted(cite.passage)}</p>`)
      .join("");
    const queries = (cand.query_ids || []).map(esc).join(", ");
    // Search snippets are truncated mid-sentence, so they always trail off.
    const snippet = String(cand.snippet ?? "").trim().replace(/[\s.…]+$/, "");
    return `
      <article class="evidence">
        <div class="evidence-head">
          <p class="rank">Rank ${esc(item.rank)}</p>
          ${source}
        </div>
        <h3>${heading}</h3>
        <p class="meta">${esc(cand.published_on || "date unknown")} · ${esc(cand.date_check || "")}</p>
        ${field("Snippet", snippet ? `<p class="snippet">${quoted(snippet + "\u2026")}</p>` : "")}
        ${field("Relevance", `<p class="explain">${esc(item.explanation)}</p>`)}
        ${field("Quotes", cites)}
        ${field("Queries", queries ? `<p class="queries">${queries}</p>` : "")}
      </article>`;
  }).join("");

  reportEl.innerHTML = `
    <p class="disclaimer">${esc(report.disclaimer)}</p>
    <p class="report-meta">Critical date · ${esc(report.critical_date)}</p>
    ${notes ? `<ul class="note-list">${notes}</ul>` : ""}
    ${cards || `<p class="report-meta">No candidate references were ranked.</p>`}
  `;
  document.getElementById("findings").scrollIntoView({ behavior: "smooth" });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const spec = specInput.files[0];
  const claims = claimsInput.files[0];
  const criticalDate = dateInput.value;
  if (!spec || !claims || !criticalDate) {
    setStatus("Choose a specification PDF, claims file, and critical date.", true);
    return;
  }
  if (spec.size + claims.size > MAX_UPLOAD_BYTES) {
    setStatus("Uploaded files exceed the combined 5 MB size limit.", true);
    return;
  }

  pollId += 1;
  const token = pollId;
  reportEl.innerHTML = "";
  submitBtn.disabled = true;
  setStatus("Submitting specification and claims…");

  const payload = new FormData();
  payload.append("specification", spec);
  payload.append("claims", claims);
  payload.append("critical_date", criticalDate);

  try {
    const response = await fetch("/jobs", { method: "POST", body: payload });
    const body = await response.json().catch(() => ({}));
    if (token !== pollId) return;

    const jobId = body.job_id || body.detail?.job_id;
    if (jobId && response.status === 202) {
      await poll(jobId, token);
      return;
    }
    if (response.status === 502 && body.detail?.error) {
      setStatus(`Failed · ${body.detail.error}`, true);
      submitBtn.disabled = false;
      return;
    }
    setStatus(detailMessage(body), true);
    submitBtn.disabled = false;
  } catch {
    setStatus("Could not reach the intake API. Is the tunnel open?", true);
    submitBtn.disabled = false;
  }
});
