// web/static/js/app.js
// Modular UI Controller for Permission-Aware RAG

import { ApiClient } from "./api_client.js";
import { store } from "./state_store.js";
import { ConfigController, renderQueryCacheIndicator } from "./components/config_controller.js";

let configController = null;

// DOM References
const tabs = document.querySelectorAll(".nav-tab");
const views = {
  config: document.getElementById("view-config"),
  playground: document.getElementById("view-playground"),
  dataset: document.getElementById("view-dataset"),
  benchmark: document.getElementById("view-benchmark"),
  concepts: document.getElementById("view-concepts"),
};

const backendStatusBadge = document.getElementById("backend-status-badge");
const personaSelector = document.getElementById("persona-selector");
const modeButtons = document.querySelectorAll(".mode-pill");
const topkSlider = document.getElementById("topk-slider");
const topkDisplay = document.getElementById("topk-display");
const questionChipsContainer = document.getElementById("question-chips-container");
const queryInput = document.getElementById("query-input");
const btnRunQuery = document.getElementById("btn-run-query");
const btnRunText = document.getElementById("btn-run-text");
const helperScoreSummary = document.getElementById("helper-score-summary");
const linkGotoConcepts = document.getElementById("link-goto-concepts");

const progressiveStepper = document.getElementById("progressive-stepper");
const step1 = document.getElementById("step-1");
const step2 = document.getElementById("step-2");
const step3 = document.getElementById("step-3");
const step1Meta = document.getElementById("step-1-meta");
const step2Meta = document.getElementById("step-2-meta");
const step3Meta = document.getElementById("step-3-meta");

const answerOutput = document.getElementById("answer-output");
const goldAnswerBox = document.getElementById("gold-answer-box");
const goldAnswerText = document.getElementById("gold-answer-text");
const groundingBadge = document.getElementById("grounding-badge");
const metricsPill = document.getElementById("metrics-pill");

const docLedgerContainer = document.getElementById("doc-ledger-container");
const ledgerStats = document.getElementById("ledger-stats");

const dropzoneArea = document.getElementById("dropzone-area");
const datasetFileInput = document.getElementById("dataset-file-input");
const uploadFeedback = document.getElementById("upload-feedback");
const btnReingest = document.getElementById("btn-reingest");

const matrixTbody = document.getElementById("matrix-tbody");

// 1. Navigation Tab Switching (HCI Modularity)
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    tabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");

    Object.keys(views).forEach((v) => {
      views[v].style.display = v === target ? (v === "playground" ? "flex" : "block") : "none";
    });

    if (target === "benchmark") loadBenchmarkMetrics();
    if (target === "config" && configController) configController.refreshState();
    if (target === "dataset") loadStorageSummary();
  });
});

// 2. Persona Selection
personaSelector.addEventListener("click", (e) => {
  const card = e.target.closest(".persona-card");
  if (!card) return;
  document.querySelectorAll(".persona-card").forEach((c) => c.classList.remove("selected"));
  card.classList.add("selected");
  store.setState({ selectedUser: card.dataset.user });
});

// 3. Retrieval Mode Toggle
modeButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    modeButtons.forEach((b) => b.classList.remove("selected"));
    btn.classList.add("selected");
    const m = btn.dataset.mode;
    store.setState({ selectedMode: m });
    
    // Dynamic score helper update based on selected mode
    if (helperScoreSummary) {
      if (m === "hybrid") {
        helperScoreSummary.textContent = "Hybrid RRF fuses Dense & BM25 rank reciprocals (Cap: ~0.033). Higher denotes cross-model agreement.";
      } else {
        helperScoreSummary.textContent = "FAISS Cosine measures angular embedding proximity in 384D space (Cap: 1.000). Higher denotes semantic similarity.";
      }
    }
  });
});

// 3a. Concept Quick Navigation Link
if (linkGotoConcepts) {
  linkGotoConcepts.addEventListener("click", (e) => {
    e.preventDefault();
    const cTab = document.getElementById("tab-concepts");
    if (cTab) cTab.click();
  });
}

// 3b. Dynamic Top-K Slider
if (topkSlider) {
  topkSlider.addEventListener("input", (e) => {
    const val = parseInt(e.target.value, 10);
    if (topkDisplay) topkDisplay.textContent = val;
    store.setState({ topK: val });
  });
}

// 4. Initial System Check
async function initializeApp() {
  try {
    const status = await ApiClient.getStatus();
    backendStatusBadge.textContent = "Online | Indices Ready";
    backendStatusBadge.className = "badge badge-allowed";

    if (status.dataset && status.dataset.loaded) {
      renderQuestionChips(status.dataset.questions);
      document.getElementById("dataset-question-count").textContent = status.dataset.question_count;
      document.getElementById("dataset-filename").textContent = status.dataset.file_name;
    }

    if (!configController) {
      configController = new ConfigController();
      configController.startPolling(2000);
    }
  } catch (err) {
    backendStatusBadge.textContent = "Offline";
    backendStatusBadge.className = "badge badge-blocked";
  }
}

// 5. Render Question Chips
function renderQuestionChips(questions) {
  questionChipsContainer.innerHTML = "";
  if (!questions || questions.length === 0) {
    questionChipsContainer.innerHTML = `<div style="color: var(--text-muted); font-size: 12px;">No questions loaded in dataset.</div>`;
    return;
  }

  questions.forEach((q, idx) => {
    const chip = document.createElement("div");
    chip.className = "chip-item";
    chip.textContent = `Q${idx + 1}: ${q.question}`;
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip-item").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      queryInput.value = q.question;
      store.setState({
        activeQuestion: q.question,
        goldAnswer: q.gold_answer,
        goldSupportingFacts: q.supporting_facts || [],
      });
      goldAnswerBox.style.display = "block";
      goldAnswerText.textContent = q.gold_answer;
    });
    questionChipsContainer.appendChild(chip);
  });
}

// 6. Stepper Animation Helper (Visibility of System Status)
function resetStepper() {
  [step1, step2, step3].forEach((s) => s.classList.remove("active", "completed"));
  step1Meta.textContent = "FAISS + BM25";
  step2Meta.textContent = "AWS S3 & GCP IAM";
  step3Meta.textContent = "Gemini Fallback Engine";
}

function updateStepper(step, status, metaText) {
  const el = step === 1 ? step1 : step === 2 ? step2 : step3;
  const meta = step === 1 ? step1Meta : step === 2 ? step2Meta : step3Meta;
  if (status === "active") {
    el.classList.add("active");
  } else if (status === "completed") {
    el.classList.remove("active");
    el.classList.add("completed");
    if (metaText) meta.textContent = metaText;
  }
}

// 7. Query Execution Flow
btnRunQuery.addEventListener("click", async () => {
  const question = queryInput.value.trim();
  if (!question) {
    alert("Please select or enter a question first.");
    return;
  }

  const { selectedUser, selectedMode, goldAnswer, goldSupportingFacts } = store.getState();

  // Reset & activate Stepper
  resetStepper();
  btnRunQuery.disabled = true;
  btnRunText.textContent = "Executing Pipeline...";
  updateStepper(1, "active");

  try {
    const result = await ApiClient.runQuery({
      user: selectedUser,
      mode: selectedMode,
      top_k: topkSlider ? parseInt(topkSlider.value, 10) : 5,
      question: question,
      gold_answer: goldAnswer,
      supporting_facts: goldSupportingFacts,
    });

    // Animate Stepper completion with exact latencies
    updateStepper(1, "completed", `${result.latencies.retrieval_ms}ms (Done)`);
    updateStepper(2, "completed", `${result.latencies.authorization_ms}ms (Done)`);
    updateStepper(3, "completed", `${result.latencies.generation_ms}ms (Done)`);

    // Render Answer Card
    answerOutput.textContent = result.predicted_answer;
    if (result.grounding_status === "BLOCKED_ACCESS_DENIED") {
      groundingBadge.className = "badge badge-blocked";
      groundingBadge.textContent = "Blocked by IAM (Zero Leaks)";
    } else {
      groundingBadge.className = "badge badge-allowed";
      groundingBadge.textContent = "Allowed & Grounded";
    }

    if (result.metrics) {
      metricsPill.textContent = `EM: ${result.metrics.exact_match.toFixed(2)} | F1: ${result.metrics.f1.toFixed(2)}`;
    }

    if (result.cache_verdict) {
      renderQueryCacheIndicator(result.cache_verdict);
    }

    // Render Document Ledger
    renderDocumentLedger(result.documents, result.allowed_count, result.total_retrieved);
  } catch (err) {
    alert("Query error: " + err.message);
    resetStepper();
  } finally {
    btnRunQuery.disabled = false;
    btnRunText.textContent = "Generate LLM Response";
  }
});

// 8. Render Document Ledger
function renderDocumentLedger(docs, allowedCount, totalCount) {
  ledgerStats.textContent = `${allowedCount} / ${totalCount} Authorized`;
  docLedgerContainer.innerHTML = "";

  if (!docs || docs.length === 0) {
    docLedgerContainer.innerHTML = `<div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">No documents returned.</div>`;
    return;
  }

  docs.forEach((doc, idx) => {
    const card = document.createElement("div");
    card.className = "doc-card";

    const allowedBadge = doc.is_allowed
      ? `<span class="badge badge-allowed">Allowed</span>`
      : `<span class="badge badge-blocked">Blocked by IAM</span>`;

    const supBadge = doc.is_supporting_doc
      ? `<span class="badge badge-supporting">Supporting Doc</span>`
      : "";

    const scoreVal = doc.score !== undefined ? doc.score : 0.0;
    const scorePct = doc.score_pct !== undefined ? doc.score_pct : 50;
    const scoreMode = doc.score_mode || "Hybrid RRF";
    const scoreCap = doc.score_cap || (scoreMode.includes("RRF") ? "~0.033" : "1.000");

    const scoreTooltip = scoreMode.includes("RRF")
      ? "RRF score fuses Dense and BM25 rank reciprocals (Cap: ~0.033). Higher denotes greater cross-model agreement."
      : "Cosine similarity in 384D embedding space (Cap: 1.000). Higher denotes closer semantic vector proximity.";

    card.innerHTML = `
      <div class="doc-card-header">
        <span class="doc-name">#${idx + 1} &middot; ${doc.file_name}</span>
        <div class="doc-badges">
          <div class="doc-score-pill" title="${scoreTooltip}">
            <span>Score: <strong>${scoreVal}</strong></span>
            <div class="doc-score-bar-bg"><div class="doc-score-bar-fill" style="width: ${scorePct}%;"></div></div>
            <span style="font-size: 9px; color: var(--text-muted);">(${scoreMode})</span>
          </div>
          ${supBadge}
          ${allowedBadge}
        </div>
      </div>
      <div class="doc-meta">
        Storage Source: <strong>${doc.bucket}</strong> | Theoretical Metric Cap: <strong>${scoreCap}</strong>
      </div>
      <div class="doc-snippet">${doc.content_snippet}</div>
    `;
    docLedgerContainer.appendChild(card);
  });
}

// 9. Quantitative Benchmark Matrix Loader
async function loadBenchmarkMetrics() {
  try {
    const data = await ApiClient.getMetrics();
    matrixTbody.innerHTML = "";

    ["hybrid", "faiss"].forEach((mode) => {
      const rows = data.summary ? data.summary[mode] : null;
      if (!rows) return;

      rows.forEach((r) => {
        const tr = document.createElement("tr");
        const matchBadge = r.AccessMatch
          ? `<span class="badge badge-allowed">PASS</span>`
          : `<span class="badge badge-blocked">FAIL</span>`;

        tr.innerHTML = `
          <td><strong>${mode.toUpperCase()}</strong></td>
          <td>${r.user}</td>
          <td>${r.PermissionCoverage_pct.toFixed(2)}%</td>
          <td>${matchBadge}</td>
          <td>${(r.em ?? 0).toFixed(4)}</td>
          <td>${(r.f1 ?? 0).toFixed(4)}</td>
          <td>${(r.em_admin ?? 0).toFixed(4)}</td>
          <td>${r.total_authorized} / ${r.total_retrieved}</td>
        `;
        matrixTbody.appendChild(tr);
      });
    });
  } catch (err) {
    matrixTbody.innerHTML = `<tr><td colspan="8" style="color: var(--text-muted); text-align: center;">Failed loading benchmark metrics.</td></tr>`;
  }
}

// 10. Dataset Drag & Drop
dropzoneArea.addEventListener("click", () => datasetFileInput.click());

datasetFileInput.addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  uploadFeedback.style.display = "block";
  uploadFeedback.textContent = `Uploading and validating ${file.name}...`;
  uploadFeedback.style.color = "var(--text-secondary)";

  try {
    const res = await ApiClient.uploadDataset(file);
    uploadFeedback.textContent = res.message;
    uploadFeedback.style.color = "var(--color-allowed-text)";
    initializeApp();
  } catch (err) {
    uploadFeedback.textContent = "Error: " + err.message;
    uploadFeedback.style.color = "var(--color-blocked-text)";
  }
});

async function loadStorageSummary() {
  const awsList = document.getElementById("dataset-aws-buckets-list");
  const gcpList = document.getElementById("dataset-gcp-buckets-list");
  const docBadge = document.getElementById("storage-summary-doc-count");
  const awsRegionBadge = document.getElementById("aws-storage-region-badge");
  const gcpRegionBadge = document.getElementById("gcp-storage-region-badge");

  try {
    const summary = await ApiClient.getStorageSummary();
    if (!summary) return;

    if (docBadge) {
      docBadge.textContent = `${summary.total_documents} Total Documents`;
      docBadge.className = summary.total_documents > 0 ? "badge badge-allowed" : "badge badge-neutral";
    }

    if (awsRegionBadge && summary.aws && summary.aws.region) {
      awsRegionBadge.textContent = summary.aws.region;
    }
    if (gcpRegionBadge && summary.gcp && summary.gcp.region) {
      gcpRegionBadge.textContent = summary.gcp.region;
    }

    // Render AWS Buckets
    if (awsList && summary.aws && summary.aws.buckets) {
      if (summary.aws.buckets.length === 0) {
        awsList.innerHTML = `<div style="color: var(--text-muted); font-size: 11px;">No AWS buckets found in access model.</div>`;
      } else {
        awsList.innerHTML = summary.aws.buckets.map(b => `
          <div style="background: #ffffff; border: 1px solid #e2e8f0; padding: 7px 10px; border-radius: 5px; display: flex; align-items: center; justify-content: space-between; gap: 6px;">
            <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              <strong style="color: #0f172a; font-size: 11px;">${b.name}</strong>
              <div style="color: #64748b; font-size: 10px;">${b.policy}</div>
            </div>
            <span class="badge badge-allowed" style="font-weight: 700; font-size: 10.5px; flex-shrink: 0; box-shadow: 0 0 6px rgba(34, 197, 94, 0.4);">
              ${b.file_count} files
            </span>
          </div>
        `).join("");
      }
    }

    // Render GCP Buckets
    if (gcpList && summary.gcp && summary.gcp.buckets) {
      if (summary.gcp.buckets.length === 0) {
        gcpList.innerHTML = `<div style="color: var(--text-muted); font-size: 11px;">No GCP buckets found in access model.</div>`;
      } else {
        gcpList.innerHTML = summary.gcp.buckets.map(b => `
          <div style="background: #ffffff; border: 1px solid #e2e8f0; padding: 7px 10px; border-radius: 5px; display: flex; align-items: center; justify-content: space-between; gap: 6px;">
            <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              <strong style="color: #0f172a; font-size: 11px;">${b.name}</strong>
              <div style="color: #64748b; font-size: 10px;">${b.policy}</div>
            </div>
            <span class="badge badge-allowed" style="font-weight: 700; font-size: 10.5px; flex-shrink: 0; box-shadow: 0 0 6px rgba(34, 197, 94, 0.4);">
              ${b.file_count} files
            </span>
          </div>
        `).join("");
      }
    }
  } catch (err) {
    console.warn("Storage summary load error:", err.message);
  }
}

const ingestFeedback = document.getElementById("ingest-feedback");

if (btnReingest) {
  btnReingest.addEventListener("click", async () => {
    btnReingest.disabled = true;
    btnReingest.textContent = "Executing Multi-Cloud Pipeline (01–07)...";
    if (ingestFeedback) {
      ingestFeedback.style.display = "block";
      ingestFeedback.style.background = "#f1f5f9";
      ingestFeedback.style.color = "#334155";
      ingestFeedback.textContent = "⏳ Running Stages 01–07: generating access model, provisioning cloud resources, partitioning documents, and building dual indices...";
    }
    try {
      const res = await ApiClient.triggerIngest();
      if (ingestFeedback) {
        ingestFeedback.style.background = "#f0fdf4";
        ingestFeedback.style.color = "#15803d";
        ingestFeedback.textContent = `✔ ${res.message || "Pipeline completed successfully."}`;
      }
      await loadStorageSummary();
      await initializeApp();
    } catch (err) {
      if (ingestFeedback) {
        ingestFeedback.style.background = "#fef2f2";
        ingestFeedback.style.color = "#b91c1c";
        ingestFeedback.textContent = `✖ Ingestion error: ${err.message}`;
      }
    } finally {
      btnReingest.disabled = false;
      btnReingest.textContent = "Execute Multi-Cloud Ingestion Pipeline";
    }
  });
}

const btnTeardownPipeline = document.getElementById("btn-teardown-pipeline");

if (btnTeardownPipeline) {
  btnTeardownPipeline.addEventListener("click", async () => {
    btnTeardownPipeline.disabled = true;
    btnTeardownPipeline.textContent = "Executing Teardown Sequence...";
    if (ingestFeedback) {
      ingestFeedback.style.display = "block";
      ingestFeedback.style.background = "#fffbeb";
      ingestFeedback.style.color = "#b45309";
      ingestFeedback.textContent = "⏳ Executing complete teardown sequence (cloud deprovisioning, Auth0 user cleanup, and local data purge)...";
    }
    try {
      const res = await ApiClient.teardownPipeline();
      if (ingestFeedback) {
        ingestFeedback.style.background = "#f0fdf4";
        ingestFeedback.style.color = "#15803d";
        ingestFeedback.textContent = `✔ ${res.message || "Teardown sequence complete."}`;
      }
      await loadStorageSummary();
      await initializeApp();
    } catch (err) {
      if (ingestFeedback) {
        ingestFeedback.style.background = "#fef2f2";
        ingestFeedback.style.color = "#b91c1c";
        ingestFeedback.textContent = `✖ Teardown error: ${err.message}`;
      }
    } finally {
      btnTeardownPipeline.disabled = false;
      btnTeardownPipeline.textContent = "Clear Pipeline (Execute Teardown Sequence)";
    }
  });
}

// Start
initializeApp();
loadStorageSummary();
