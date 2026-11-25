const API_BASE = "/api/v1";
const SESSION_STORAGE_KEY = "iara_session_id";

const state = {
  sessionId: null,
  isFinished: false,
  isBusy: false,
  fastTurns: 0,
  llmTurns: 0,
};

const els = {
  clinicName: document.getElementById("clinic-name"),
  modelBadge: document.getElementById("model-badge"),
  messages: document.getElementById("messages"),
  quickReplies: document.getElementById("quick-replies"),
  form: document.getElementById("chat-form"),
  input: document.getElementById("message-input"),
  sendBtn: document.getElementById("send-btn"),
  charCounter: document.getElementById("char-counter"),
  newSessionBtn: document.getElementById("new-session-btn"),
  deleteSessionBtn: document.getElementById("delete-session-btn"),
  statusPill: document.getElementById("status-pill"),
  progressText: document.getElementById("progress-text"),
  progressFill: document.getElementById("progress-fill"),
  statFast: document.getElementById("stat-fast"),
  statLlm: document.getElementById("stat-llm"),
  rowInsurancePayer: document.getElementById("row-insurance_payer"),
  rowInsuranceId: document.getElementById("row-insurance_id"),
};

const STATUS_META = {
  collecting: { label: "Em coleta", className: "status-pill status-collecting" },
  confirming: { label: "Aguardando confirmação", className: "status-pill status-confirming" },
  scheduling: { label: "Agendamento", className: "status-pill status-confirming" },
  waiting_human: { label: "Aguardando atendente", className: "status-pill status-confirming" },
  human_takeover: { label: "Atendimento humano", className: "status-pill status-collecting" },
  completed: { label: "Concluído", className: "status-pill status-completed" },
  emergency: { label: "Emergência", className: "status-pill status-emergency" },
};

function formatDateBr(isoDate) {
  if (!isoDate) return null;
  const parts = isoDate.split("-");
  if (parts.length !== 3) return isoDate;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

function setBusy(busy) {
  state.isBusy = busy;
  els.input.disabled = busy || state.isFinished;
  els.sendBtn.disabled = busy || state.isFinished;
  els.quickReplies.querySelectorAll("button").forEach((btn) => {
    btn.disabled = busy || state.isFinished;
  });
}

function appendMessage(actor, text, { usedLlm = false, isEmergency = false } = {}) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg msg-${actor}${isEmergency ? " msg-emergency" : ""}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.appendChild(bubble);

  if (actor !== "user") {
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    const pill = document.createElement("span");
    pill.className = `token-pill ${usedLlm ? "token-llm" : "token-fast"}`;
    pill.textContent = usedLlm ? "1 chamada · LLM" : "0 tokens · fast-path";
    meta.appendChild(pill);
    wrapper.appendChild(meta);
  }

  els.messages.appendChild(wrapper);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function updateSummary(status, patientInfo, missingFields) {
  const statusInfo = STATUS_META[status] || STATUS_META.collecting;
  els.statusPill.textContent = statusInfo.label;
  els.statusPill.className = statusInfo.className;

  const isPrivate = patientInfo.coverage_type === "particular";
  els.rowInsurancePayer.classList.toggle("hidden-row", isPrivate);
  els.rowInsuranceId.classList.toggle("hidden-row", isPrivate);

  const totalFields = isPrivate ? 5 : 7;
  const filledCount = Math.max(0, totalFields - missingFields.length);
  const pct = Math.round((filledCount / totalFields) * 100);
  els.progressText.textContent = `${pct}%`;
  els.progressFill.style.width = `${pct}%`;

  const displayValues = {
    full_name: patientInfo.full_name,
    date_of_birth: formatDateBr(patientInfo.date_of_birth),
    address: patientInfo.address,
    coverage_type:
      patientInfo.coverage_type === "convenio"
        ? "Convênio"
        : patientInfo.coverage_type === "particular"
          ? "Particular"
          : null,
    insurance_payer: patientInfo.insurance_payer,
    insurance_id: patientInfo.insurance_id,
    chief_complaint: patientInfo.chief_complaint,
  };

  for (const [key, val] of Object.entries(displayValues)) {
    const dd = document.getElementById(`field-${key}`);
    if (!dd) continue;
    if (val) {
      dd.textContent = val;
      dd.classList.remove("empty");
    } else {
      dd.textContent = "Pendente";
      dd.classList.add("empty");
    }
  }
}

function renderQuickReplies(options) {
  els.quickReplies.innerHTML = "";
  if (!options || options.length === 0 || state.isFinished) {
    els.quickReplies.hidden = true;
    return;
  }

  options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip-btn";
    btn.textContent = opt.label;
    btn.disabled = state.isBusy;
    btn.addEventListener("click", () => sendMessage(opt.value));
    els.quickReplies.appendChild(btn);
  });
  els.quickReplies.hidden = false;
}

function updateTelemetry() {
  els.statFast.textContent = String(state.fastTurns);
  els.statLlm.textContent = String(state.llmTurns);
}

function hydrateSession(data) {
  state.sessionId = data.session_id;
  state.isFinished = data.is_finished;
  state.fastTurns = 0;
  state.llmTurns = 0;
  localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);

  els.clinicName.textContent = `${data.clinic_name} · Atendimento por Iara`;
  els.messages.innerHTML = "";

  data.messages.forEach((item, index) => {
    const isEmergencyMsg =
      data.status === "emergency" && index === data.messages.length - 1 && item.actor !== "user";
    appendMessage(item.actor, item.message, {
      usedLlm: item.used_llm,
      isEmergency: isEmergencyMsg,
    });
    if (item.actor !== "user" && index > 0) {
      if (item.used_llm) {
        state.llmTurns += 1;
      } else {
        state.fastTurns += 1;
      }
    }
  });

  updateTelemetry();
  updateSummary(data.status, data.patient_info, data.missing_fields);
  renderQuickReplies(data.quick_replies);
  els.input.placeholder = data.is_finished
    ? "Atendimento encerrado. Clique em 'Nova triagem' para reiniciar."
    : "Digite sua resposta aqui...";
  setBusy(false);
}

async function fetchHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) return;
    const data = await res.json();
    els.clinicName.textContent = `${data.clinic_name} · Atendimento por Iara`;
    els.modelBadge.textContent = `${data.provider}: ${data.model}`;
  } catch {
    els.modelBadge.textContent = "API indisponível";
  }
}

async function startNewSession() {
  setBusy(true);
  try {
    const res = await fetch(`${API_BASE}/sessions`, { method: "POST" });
    if (!res.ok) throw new Error("Falha ao iniciar sessão");
    const data = await res.json();
    hydrateSession(data);
    els.input.focus();
  } catch (err) {
    appendMessage("system", `Erro ao iniciar atendimento: ${err.message}`);
    setBusy(false);
  }
}

async function restoreOrCreateSession() {
  const existingId = localStorage.getItem(SESSION_STORAGE_KEY);
  if (existingId) {
    try {
      const res = await fetch(`${API_BASE}/sessions/${existingId}`);
      if (res.ok) {
        const data = await res.json();
        hydrateSession(data);
        return;
      }
    } catch {
      // Fallback to creating a fresh session
    }
    localStorage.removeItem(SESSION_STORAGE_KEY);
  }
  await startNewSession();
}

async function sendMessage(text) {
  const trimmed = text.trim();
  if (!trimmed || !state.sessionId || state.isBusy || state.isFinished) return;

  appendMessage("user", trimmed);
  els.input.value = "";
  els.charCounter.textContent = "0/1000";
  setBusy(true);

  try {
    const res = await fetch(`${API_BASE}/sessions/${state.sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: trimmed }),
    });
    if (!res.ok) {
      throw new Error(`Erro HTTP ${res.status}`);
    }
    const data = await res.json();
    const { reply } = data;
    state.isFinished = reply.is_finished;

    if (reply.message) {
      if (reply.used_llm) {
        state.llmTurns += 1;
      } else {
        state.fastTurns += 1;
      }

      appendMessage(reply.error ? "system" : "assistant", reply.message, {
        usedLlm: reply.used_llm,
        isEmergency: reply.status === "emergency",
      });
    }
    updateTelemetry();
    updateSummary(reply.status, reply.patient_info, data.missing_fields);
    renderQuickReplies(data.quick_replies);

    if (reply.is_finished) {
      els.input.placeholder = "Atendimento encerrado. Clique em 'Nova triagem' para reiniciar.";
    }
  } catch (err) {
    appendMessage("system", `Não foi possível enviar sua mensagem (${err.message}).`);
  } finally {
    setBusy(false);
    if (!state.isFinished) els.input.focus();
  }
}

async function deleteCurrentSession() {
  if (!state.sessionId || state.isBusy) return;
  setBusy(true);
  try {
    await fetch(`${API_BASE}/sessions/${state.sessionId}`, { method: "DELETE" });
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } finally {
    await startNewSession();
  }
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(els.input.value);
});

els.input.addEventListener("input", () => {
  els.charCounter.textContent = `${els.input.value.length}/1000`;
});

els.newSessionBtn.addEventListener("click", () => {
  localStorage.removeItem(SESSION_STORAGE_KEY);
  startNewSession();
});

els.deleteSessionBtn.addEventListener("click", deleteCurrentSession);

(async function init() {
  await fetchHealth();
  await restoreOrCreateSession();
})();
