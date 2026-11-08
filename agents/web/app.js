const messagesElement = document.querySelector("#messages");
const form = document.querySelector("#composer");
const promptInput = document.querySelector("#prompt");
const taskSelect = document.querySelector("#task");
const modelSelect = document.querySelector("#model");
const speechVoiceSelect = document.querySelector("#speech-voice");
const speechVoiceNotice = document.querySelector("#speech-voice-notice");
const speechRate = document.querySelector("#speech-rate");
const speechRateValue = document.querySelector("#speech-rate-value");
const repeatSpeechButton = document.querySelector("#repeat-speech");
const stopSpeechButton = document.querySelector("#stop-speech");
const conversationSelect = document.querySelector("#conversation-select");
const newConversationButton = document.querySelector("#new-conversation");
const renameConversationButton = document.querySelector("#rename-conversation");
const galleryToggleButton = document.querySelector("#gallery-toggle");
const galleryRefreshButton = document.querySelector("#gallery-refresh");
const galleryPanel = document.querySelector("#gallery-panel");
const galleryGrid = document.querySelector("#gallery-grid");
const diagnosticsToggleButton = document.querySelector("#diagnostics-toggle");
const diagnosticsRefreshButton = document.querySelector("#diagnostics-refresh");
const diagnosticsPanel = document.querySelector("#diagnostics-panel");
const diagnosticsGrid = document.querySelector("#diagnostics-grid");
const diagnosticsCheckedAt = document.querySelector("#diagnostics-checked-at");
const chatPanel = document.querySelector(".chat-panel");
const routeHint = document.querySelector("#route-hint");
const imageCheckbox = document.querySelector("#generate-image");
const videoCheckbox = document.querySelector("#generate-video");
const speakCheckbox = document.querySelector("#speak-answer");
const sendButton = document.querySelector("#send");
const recordButton = document.querySelector("#record");
const clearHistoryButton = document.querySelector("#clear-history");
const activity = document.querySelector("#activity");
const activityMessage = document.querySelector("#activity-message");
const cancelJobButton = document.querySelector("#cancel-job");
const serviceStatus = document.querySelector("#service-status");
const themeToggle = document.querySelector("#theme-toggle");
const themeIcon = document.querySelector("#theme-icon");
const themeLabel = document.querySelector("#theme-label");

const conversation = [];
const preferencesKey = "ai-projects-local-agent-preferences";
let preferencesWarning = "";
let savedTheme = null;
let savedModel = "";
let savedSpeechVoice = "";
let savedConversationId = null;
let activeConversationId = null;
let availableSpeechVoices = [];
let activeSpeech = null;
let lastSpeech = null;
let activePanel = null;
let busy = false;
let activeMediaJobId = null;
let cancelRequested = false;
let recorder = null;
let mediaStream = null;
let recordTimeout = null;
let audioChunks = [];

function setStatus(state, text) {
  serviceStatus.className = `service-status ${state}`;
  serviceStatus.lastElementChild.textContent = text;
}

function currentTheme() {
  if (savedTheme) return savedTheme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function updateThemeControl() {
  const dark = currentTheme() === "dark";
  themeToggle.setAttribute("aria-pressed", String(dark));
  themeToggle.setAttribute(
    "aria-label",
    `Alternar para o tema ${dark ? "claro" : "escuro"}`,
  );
  themeIcon.textContent = dark ? "☀" : "☾";
  themeLabel.textContent = `Tema ${dark ? "claro" : "escuro"}`;
}

function applyTheme(theme) {
  savedTheme = theme;
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  updateThemeControl();
}

function setBusy(value, message = "") {
  busy = value;
  sendButton.disabled = value;
  promptInput.disabled = value;
  taskSelect.disabled = value;
  modelSelect.disabled = value;
  speechVoiceSelect.disabled = value || speechVoiceSelect.options.length < 2;
  repeatSpeechButton.disabled = value || !lastSpeech;
  stopSpeechButton.disabled = !activeSpeech;
  imageCheckbox.disabled = value;
  videoCheckbox.disabled = value;
  clearHistoryButton.disabled = value;
  conversationSelect.disabled = value || conversationSelect.options.length < 1;
  newConversationButton.disabled = value;
  renameConversationButton.disabled = value || activeConversationId === null;
  galleryToggleButton.disabled = value;
  galleryRefreshButton.disabled = value;
  diagnosticsToggleButton.disabled = value;
  diagnosticsRefreshButton.disabled = value;
  setActivity(message);
}

function setActivity(message, cancelable = false) {
  activity.hidden = !message;
  activityMessage.textContent = message;
  cancelJobButton.hidden = !cancelable;
  cancelJobButton.disabled = cancelRequested;
}

async function waitForMediaJob(jobId) {
  activeMediaJobId = jobId;
  cancelRequested = false;
  cancelJobButton.hidden = false;
  while (true) {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível consultar a geração.");
    const job = payload.job;
    if (!job || typeof job.status !== "string") {
      throw new Error("O serviço retornou o estado de tarefa em formato inválido.");
    }
    if (job.cancel_error) {
      cancelRequested = false;
      setActivity(
        `Não foi possível confirmar a interrupção: ${job.cancel_error}`,
        ["queued", "running", "cancelling"].includes(job.status),
      );
    } else {
      setActivity(job.progress || "A geração local está em andamento.", [
        "queued",
        "running",
        "cancelling",
      ].includes(job.status));
    }
    if (job.status === "completed") return job.result;
    if (job.status === "failed") {
      throw new Error(job.error || "A geração local falhou.");
    }
    if (job.status === "cancelled") {
      throw new Error("A geração de mídia foi cancelada.");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 900));
  }
}

async function cancelMediaJob() {
  if (!activeMediaJobId || cancelRequested) return;
  cancelRequested = true;
  setActivity("Solicitando cancelamento da geração local…", true);
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(activeMediaJobId)}`, {
      method: "DELETE",
    });
    const payload = await response.json();
    if (!response.ok) {
      cancelRequested = false;
      setActivity(payload.error || "Não foi possível cancelar a geração.", true);
      return;
    }
    if (payload.job?.cancel_error) {
      cancelRequested = false;
      setActivity(
        `Não foi possível confirmar a interrupção: ${payload.job.cancel_error}`,
        true,
      );
    } else {
      setActivity(
        payload.job?.progress || "Aguardando a interrupção da geração local…",
        true,
      );
    }
  } catch (error) {
    cancelRequested = false;
    setActivity(`Falha ao solicitar cancelamento: ${error.message}`, true);
  }
}

function appendMessage(
  role,
  text,
  meta = "",
  imageUrl = null,
  imageError = "",
  videoUrl = null,
  videoError = "",
) {
  const welcome = messagesElement.querySelector(".welcome-card");
  if (welcome) welcome.remove();

  const article = document.createElement("article");
  article.className = `message ${role}`;
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = role === "user" ? "Você" : "Agente";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  if (imageUrl) {
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = "Imagem gerada localmente";
    image.loading = "lazy";
    bubble.append(image);
  }
  if (videoUrl) {
    const video = document.createElement("video");
    video.controls = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", "Vídeo gerado localmente");
    const source = document.createElement("source");
    source.src = videoUrl;
    source.type = "video/webm";
    video.append(source);
    bubble.append(video);
  }
  if (imageError) {
    const error = document.createElement("p");
    error.className = "image-error";
    error.textContent = `A geração da imagem falhou: ${imageError}`;
    bubble.append(error);
  }
  if (videoError) {
    const error = document.createElement("p");
    error.className = "video-error";
    error.textContent = `A geração do vídeo falhou: ${videoError}`;
    bubble.append(error);
  }
  article.append(label, bubble);
  if (meta) {
    const detail = document.createElement("span");
    detail.className = "message-meta";
    detail.textContent = meta;
    article.append(detail);
  }
  messagesElement.append(article);
  messagesElement.scrollTop = messagesElement.scrollHeight;
  return bubble;
}

function restorePreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(preferencesKey) || "{}");
    if (saved.theme === "light" || saved.theme === "dark") {
      applyTheme(saved.theme);
    } else {
      applyTheme(null);
    }
    if (saved.task === "text" || saved.task === "code") taskSelect.value = saved.task;
    if (typeof saved.model === "string") savedModel = saved.model;
    if (typeof saved.speechVoice === "string") savedSpeechVoice = saved.speechVoice;
    if (Number.isInteger(saved.conversationId)) savedConversationId = saved.conversationId;
    if (typeof saved.speechRate === "number" && saved.speechRate >= 0.8 && saved.speechRate <= 1.25) {
      speechRate.value = String(saved.speechRate);
    }
    if (typeof saved.generateImage === "boolean") imageCheckbox.checked = saved.generateImage;
    if (typeof saved.generateVideo === "boolean") videoCheckbox.checked = saved.generateVideo;
    if (imageCheckbox.checked && videoCheckbox.checked) videoCheckbox.checked = false;
    if (typeof saved.speakAnswer === "boolean") speakCheckbox.checked = saved.speakAnswer;
  } catch {
    applyTheme(null);
    preferencesWarning = "As preferências locais não puderam ser lidas; usando os padrões.";
  }
}

function savePreferences() {
  try {
    localStorage.setItem(preferencesKey, JSON.stringify({
      task: taskSelect.value,
      theme: savedTheme,
      model: modelSelect.value,
      speechVoice: speechVoiceSelect.value,
      speechRate: Number(speechRate.value),
      conversationId: activeConversationId,
      generateImage: imageCheckbox.checked,
      generateVideo: videoCheckbox.checked,
      speakAnswer: speakCheckbox.checked,
    }));
  } catch (error) {
    setStatus("error", `Não foi possível salvar as preferências: ${error.message}`);
  }
}

async function loadModels() {
  try {
    const response = await fetch("/api/models", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível consultar os modelos.");
    const selected = modelSelect.value || savedModel;
    modelSelect.replaceChildren(new Option("Automático pelo perfil", ""));
    for (const model of payload.models) {
      modelSelect.add(new Option(model, model));
    }
    if ([...modelSelect.options].some((option) => option.value === selected)) {
      modelSelect.value = selected;
    }
    const defaults = payload.defaults;
    routeHint.textContent = `Texto: ${defaults.text} · Código: ${defaults.code}`;
    setStatus("ready", `${payload.models.length} modelo(s) Ollama local(is)`);
  } catch (error) {
    setStatus("error", error.message);
  }
}

async function loadSpeechVoices() {
  try {
    const response = await fetch("/api/voices", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível consultar as vozes locais.");
    const voices = payload.voices.filter((voice) => voice.available);
    if (!voices.length) throw new Error("Nenhuma voz Piper está instalada.");
    availableSpeechVoices = voices;
    speechVoiceSelect.replaceChildren();
    for (const voice of voices) {
      const gender = voice.gender ? ` · ${voice.gender}` : "";
      speechVoiceSelect.add(
        new Option(
          `${voice.language} · ${voice.name}${gender} · ${voice.quality}`,
          voice.id,
        ),
      );
    }
    const selected = voices.some((voice) => voice.id === savedSpeechVoice)
      ? savedSpeechVoice
      : payload.default;
    speechVoiceSelect.value = voices.some((voice) => voice.id === selected)
      ? selected
      : voices[0].id;
    speechVoiceSelect.disabled = false;
    updateSpeechVoiceNotice();
  } catch (error) {
    availableSpeechVoices = [];
    speechVoiceNotice.hidden = true;
    speechVoiceSelect.replaceChildren(new Option(`Vozes indisponíveis: ${error.message}`, ""));
    speechVoiceSelect.disabled = true;
  }
}

function updateSpeechVoiceNotice() {
  const voice = availableSpeechVoices.find(
    (item) => item.id === speechVoiceSelect.value,
  );
  speechVoiceNotice.textContent = voice?.license_notice || "";
  speechVoiceNotice.hidden = !voice?.license_notice;
}

function showWelcome() {
  messagesElement.replaceChildren();
  const welcome = document.createElement("div");
  welcome.className = "welcome-card";
  const icon = document.createElement("span");
  icon.className = "welcome-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "✦";
  const title = document.createElement("h2");
  title.textContent = "Como posso ajudar?";
  const description = document.createElement("p");
  description.textContent = "Escolha um perfil, escreva uma mensagem ou use o microfone.";
  welcome.append(icon, title, description);
  messagesElement.append(welcome);
}

async function refreshConversationList(preferredId = activeConversationId) {
  const response = await fetch("/api/conversations", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Não foi possível carregar as conversas.");
  if (!Array.isArray(payload.conversations) || payload.conversations.length === 0) {
    throw new Error("O serviço não retornou conversas locais.");
  }
  const selected = payload.conversations.find(
    (item) => item.id === Number(preferredId),
  ) || payload.conversations[0];
  conversationSelect.replaceChildren();
  for (const item of payload.conversations) {
    const turns = Math.floor(item.message_count / 2);
    const count = turns ? ` (${turns})` : "";
    conversationSelect.add(new Option(`${item.title}${count}`, String(item.id)));
  }
  activeConversationId = selected.id;
  conversationSelect.value = String(activeConversationId);
  conversationSelect.disabled = busy;
  clearHistoryButton.disabled = busy;
  renameConversationButton.disabled = busy;
  savePreferences();
  return payload.conversations;
}

async function loadConversations() {
  try {
    await refreshConversationList(savedConversationId);
    return await loadHistory(activeConversationId);
  } catch (error) {
    showWelcome();
    appendMessage("assistant", `Não foi possível carregar as conversas locais: ${error.message}`);
    return false;
  }
}

async function loadHistory(conversationId) {
  conversation.length = 0;
  showWelcome();
  try {
    const response = await fetch(
      `/api/history?conversation_id=${encodeURIComponent(conversationId)}`,
      { cache: "no-store" },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível carregar o histórico.");
    for (const message of payload.messages) {
      conversation.push({ role: message.role, content: message.content });
      const meta = message.role === "assistant" && message.model
        ? `Modelo: ${message.model} · Perfil: ${message.task}`
        : "";
      appendMessage(
        message.role,
        message.content,
        meta,
        message.image_url,
        message.image_error,
        message.video_url,
        message.video_error,
      );
    }
    return true;
  } catch (error) {
    showWelcome();
    appendMessage("assistant", `Não foi possível restaurar o histórico local: ${error.message}`);
    return false;
  }
}

function showGalleryMessage(message) {
  const notice = document.createElement("p");
  notice.className = "gallery-empty";
  notice.textContent = message;
  galleryGrid.replaceChildren(notice);
}

function renderGallery(items) {
  galleryGrid.replaceChildren();
  if (!items.length) {
    showGalleryMessage("Ainda não há imagens ou vídeos registrados nas conversas.");
    return;
  }
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "gallery-item";
    let media;
    if (item.type === "image") {
      media = document.createElement("img");
      media.alt = item.prompt || "Imagem gerada localmente";
      media.loading = "lazy";
    } else if (item.type === "video") {
      media = document.createElement("video");
      media.controls = true;
      media.preload = "metadata";
      media.setAttribute("aria-label", item.prompt || "Vídeo gerado localmente");
    } else {
      continue;
    }
    media.src = item.url;
    media.addEventListener("error", () => {
      const unavailable = document.createElement("p");
      unavailable.className = "gallery-empty";
      unavailable.textContent = "Este arquivo de mídia não está mais disponível.";
      media.replaceWith(unavailable);
    }, { once: true });
    const info = document.createElement("div");
    info.className = "gallery-item-info";
    const prompt = document.createElement("p");
    prompt.textContent = item.prompt || "Prompt não registrado";
    const timestamp = new Date(item.created_at);
    const date = Number.isNaN(timestamp.getTime())
      ? item.created_at
      : timestamp.toLocaleString("pt-BR");
    const details = document.createElement("small");
    details.textContent = `${item.conversation_title} · ${date}`;
    info.append(prompt, details);
    card.append(media, info);
    galleryGrid.append(card);
  }
}

async function loadGallery() {
  try {
    const response = await fetch("/api/gallery", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível carregar a galeria.");
    if (!Array.isArray(payload.items)) throw new Error("O serviço retornou uma galeria inválida.");
    renderGallery(payload.items);
  } catch (error) {
    showGalleryMessage(`Não foi possível carregar a galeria local: ${error.message}`);
  }
}

function setActivePanel(panel) {
  activePanel = activePanel === panel ? null : panel;
  galleryPanel.hidden = activePanel !== "gallery";
  diagnosticsPanel.hidden = activePanel !== "diagnostics";
  chatPanel.classList.toggle("tool-open", activePanel !== null);
  galleryToggleButton.textContent = activePanel === "gallery"
    ? "Voltar à conversa"
    : "Galeria de mídia";
  diagnosticsToggleButton.textContent = activePanel === "diagnostics"
    ? "Voltar à conversa"
    : "Diagnóstico local";
  galleryToggleButton.setAttribute("aria-expanded", String(activePanel === "gallery"));
  diagnosticsToggleButton.setAttribute(
    "aria-expanded",
    String(activePanel === "diagnostics"),
  );
}

async function toggleGallery() {
  if (busy) return;
  setActivePanel("gallery");
  if (activePanel === "gallery") await loadGallery();
}

const diagnosticStateLabels = {
  online: "Online",
  offline: "Offline",
  error: "Erro",
  available: "Disponível",
  unavailable: "Indisponível",
};

function appendDiagnosticCard(title, state, message, details = []) {
  const card = document.createElement("article");
  card.className = "diagnostic-card";
  const heading = document.createElement("div");
  heading.className = "diagnostic-title";
  const name = document.createElement("h3");
  name.textContent = title;
  const badge = document.createElement("span");
  badge.className = `diagnostic-state ${state}`;
  badge.textContent = diagnosticStateLabels[state] || "Desconhecido";
  heading.append(name, badge);
  card.append(heading);
  const summary = document.createElement("p");
  summary.textContent = message;
  card.append(summary);
  if (details.length) {
    const list = document.createElement("ul");
    for (const detail of details) {
      const item = document.createElement("li");
      item.textContent = detail;
      list.append(item);
    }
    card.append(list);
  }
  diagnosticsGrid.append(card);
}

function renderDiagnostics(payload) {
  if (!payload || !payload.services || !payload.hardware) {
    throw new Error("O serviço retornou um diagnóstico inválido.");
  }
  diagnosticsGrid.replaceChildren();
  const { services, hardware } = payload;
  const ollama = services.ollama || {};
  const profiles = ollama.profiles || {};
  const ollamaDetails = [];
  if (Array.isArray(ollama.models)) ollamaDetails.push(`Modelos: ${ollama.models.join(", ") || "nenhum instalado"}`);
  for (const [label, profile] of [["Texto", profiles.text], ["Código", profiles.code]]) {
    if (profile?.model) {
      ollamaDetails.push(
        `${label}: ${profile.model}${profile.installed ? "" : " (não instalado)"}`,
      );
    }
  }
  appendDiagnosticCard(
    "Ollama",
    ollama.status || "unavailable",
    ollama.message || "Não foi possível consultar os modelos locais.",
    ollamaDetails,
  );

  const whisper = services.whisper || {};
  appendDiagnosticCard(
    "Whisper",
    whisper.status || "unavailable",
    whisper.message || "Estado não informado.",
    [
      whisper.model ? `Modelo: ${whisper.model}` : "",
      whisper.device ? `Dispositivo: ${whisper.device}` : "",
    ].filter(Boolean),
  );

  const speech = services.speech || {};
  appendDiagnosticCard(
    "Piper / Kokoro",
    speech.status || "unavailable",
    speech.message || "Estado não informado.",
    [
      Number.isInteger(speech.available_voices)
        ? `Vozes disponíveis: ${speech.available_voices}`
        : "",
      speech.default_voice ? `Voz padrão: ${speech.default_voice}` : "",
      Array.isArray(speech.engines) && speech.engines.length
        ? `Mecanismos: ${speech.engines.join(", ")}`
        : "",
    ].filter(Boolean),
  );

  const comfyui = services.comfyui || {};
  const devices = Array.isArray(comfyui.devices) ? comfyui.devices : [];
  appendDiagnosticCard(
    "ComfyUI",
    comfyui.status || "unavailable",
    comfyui.message || "Não foi possível consultar o serviço de mídia.",
    [
      comfyui.checkpoint ? `Checkpoint configurado: ${comfyui.checkpoint}` : "",
      ...devices.flatMap((device) => (
        device && typeof device.name === "string" ? [device.name] : []
      )),
    ].filter(Boolean),
  );

  const memory = hardware.memory || {};
  const cpuCores = hardware.cpu_logical_cores;
  appendDiagnosticCard(
    "Processador",
    Number.isInteger(cpuCores) ? "available" : "unavailable",
    Number.isInteger(cpuCores)
      ? `${cpuCores} núcleos lógicos disponíveis ao sistema.`
      : "O sistema não informou a contagem de processadores.",
  );
  const memoryDetails = [];
  if (Number.isInteger(memory.total_mb)) memoryDetails.push(`Total: ${memory.total_mb} MiB`);
  if (Number.isInteger(memory.available_mb)) memoryDetails.push(`Disponível: ${memory.available_mb} MiB`);
  appendDiagnosticCard(
    "Memória do sistema",
    memory.total_mb ? "available" : "unavailable",
    memory.total_mb ? "Informação obtida do sistema local." : "O sistema não informou a memória.",
    memoryDetails,
  );

  const gpu = hardware.gpu || {};
  appendDiagnosticCard(
    "GPU / VRAM",
    gpu.status || "unavailable",
    gpu.message || gpu.name || "Métricas de GPU não disponíveis.",
    [
      Number.isInteger(gpu.vram_total_mb) ? `VRAM total: ${gpu.vram_total_mb} MiB` : "",
      Number.isInteger(gpu.vram_free_mb) ? `VRAM livre: ${gpu.vram_free_mb} MiB` : "",
    ].filter(Boolean),
  );

  const disk = hardware.disk || {};
  appendDiagnosticCard(
    "Armazenamento",
    disk.status || "unavailable",
    disk.message || disk.path || "Não foi possível consultar o disco.",
    [
      Number.isFinite(disk.total_gb) ? `Total: ${disk.total_gb} GB` : "",
      Number.isFinite(disk.free_gb) ? `Livre: ${disk.free_gb} GB` : "",
    ].filter(Boolean),
  );
  const checkedAt = new Date(payload.checked_at);
  diagnosticsCheckedAt.textContent = Number.isNaN(checkedAt.getTime())
    ? "Estado dos serviços e recursos deste computador."
    : `Atualizado em ${checkedAt.toLocaleString("pt-BR")}`;
}

async function loadDiagnostics() {
  try {
    const response = await fetch("/api/diagnostics", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível carregar o diagnóstico.");
    renderDiagnostics(payload);
  } catch (error) {
    diagnosticsGrid.replaceChildren();
    appendDiagnosticCard(
      "Diagnóstico local",
      "error",
      `Não foi possível carregar o diagnóstico: ${error.message}`,
    );
  }
}

async function toggleDiagnostics() {
  if (busy) return;
  setActivePanel("diagnostics");
  if (activePanel === "diagnostics") await loadDiagnostics();
}

async function createConversation() {
  if (busy) return;
  setBusy(true, "Criando conversa local…");
  try {
    const response = await fetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "Nova conversa" }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível criar a conversa.");
    activeConversationId = payload.conversation.id;
    conversation.length = 0;
    showWelcome();
    await refreshConversationList(activeConversationId);
    promptInput.focus();
  } catch (error) {
    appendMessage("assistant", `Não foi possível criar uma conversa: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function renameConversation() {
  if (busy || activeConversationId === null) return;
  const currentTitle = conversationSelect.selectedOptions[0]?.textContent.replace(/\s+\(\d+\)$/, "") || "";
  const title = window.prompt("Nome da conversa:", currentTitle);
  if (title === null) return;
  if (!title.trim() || title.trim().length > 72) {
    appendMessage("assistant", "O nome da conversa deve conter de 1 a 72 caracteres.");
    return;
  }
  setBusy(true, "Renomeando conversa…");
  try {
    const response = await fetch(`/api/conversations/${activeConversationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível renomear a conversa.");
    await refreshConversationList(activeConversationId);
  } catch (error) {
    appendMessage("assistant", `Não foi possível renomear a conversa: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function receiveStream(response, onToken) {
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || "Falha ao consultar o agente.");
  }
  if (!response.body) throw new Error("O navegador não oferece suporte a streaming.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  const consumeLine = (line) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "token" && typeof event.token === "string") {
      onToken(event.token);
    } else if (event.type === "done" && event.result) {
      result = event.result;
    } else if (event.type === "error") {
      throw new Error(event.error || "O streaming local falhou.");
    } else {
      throw new Error("O serviço retornou um evento de streaming inválido.");
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) consumeLine(line);
    if (done) break;
  }
  if (buffer.trim()) consumeLine(buffer);
  if (!result) throw new Error("O serviço encerrou o streaming sem concluir a resposta.");
  return result;
}

async function sendMessage(text) {
  const content = text.trim();
  if (!content || busy) return;
  appendMessage("user", content);
  promptInput.value = "";
  const videoIntent = /\b(?:gere|gera|crie|cria|produza|produz|faça|faz|generate|create|make)\b.{0,80}\b(?:vídeo|video)\b/i.test(content);
  const imageIntent = /\b(?:gere|gera|crie|cria|desenhe|desenha|produza|produz|faça|faz|generate|create|draw)\b.{0,80}\b(?:imagem|ilustração|figura|foto|image|illustration|picture)\b/i.test(content);
  if (videoIntent) imageCheckbox.checked = false;
  else if (imageIntent) videoCheckbox.checked = false;
  savePreferences();
  const mediaRequested = videoCheckbox.checked || videoIntent
    || imageCheckbox.checked || imageIntent;
  setBusy(
    true,
    videoCheckbox.checked || videoIntent
      ? "Gerando vídeo local; isso pode levar alguns minutos…"
      : mediaRequested
        ? "O modelo local está preparando a mídia…"
        : "Aguardando os primeiros tokens do modelo local…",
  );

  try {
    const context = [...conversation, { role: "user", content }];
    let contextSize = context.reduce((total, message) => total + message.content.length, 0);
    while (context.length > 80 || contextSize > 60000) {
      contextSize -= context.shift().content.length;
    }
    const requestBody = {
      messages: context,
      task: taskSelect.value,
      model: modelSelect.value || null,
      conversation_id: activeConversationId,
      generate_image: imageCheckbox.checked,
      generate_video: videoCheckbox.checked,
    };
    let payload;
    let streamedBubble = null;
    if (mediaRequested) {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Falha ao consultar o agente.");
      if (response.status === 202) {
        if (!payload.job || typeof payload.job.id !== "string") {
          throw new Error("O serviço retornou uma tarefa de mídia inválida.");
        }
        payload = await waitForMediaJob(payload.job.id);
      }
    } else {
      streamedBubble = appendMessage("assistant", "");
      payload = await receiveStream(await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: context,
          task: taskSelect.value,
          model: modelSelect.value || null,
          conversation_id: activeConversationId,
        }),
      }), (token) => {
        streamedBubble.textContent += token;
        messagesElement.scrollTop = messagesElement.scrollHeight;
      });
    }
    conversation.push({ role: "user", content });
    conversation.push({ role: "assistant", content: payload.answer });
    try {
      await refreshConversationList(activeConversationId);
    } catch (error) {
      setStatus("error", `Resposta salva, mas não foi possível atualizar as conversas: ${error.message}`);
    }
    if (streamedBubble) {
      const detail = document.createElement("span");
      detail.className = "message-meta";
      detail.textContent = `Modelo: ${payload.model} · Perfil: ${payload.task}`;
      streamedBubble.parentElement.append(detail);
    } else {
      appendMessage(
        "assistant",
        payload.answer,
        `Modelo: ${payload.model} · Perfil: ${payload.task}`,
        payload.image_url,
        payload.image_error,
        payload.video_url,
        payload.video_error,
      );
    }
    if (speakCheckbox.checked) {
      try {
        await speak(payload.answer, speechVoiceSelect.value);
      } catch (error) {
        appendMessage(
          "assistant",
          `A resposta está disponível, mas a síntese de voz falhou: ${error.message}`,
        );
      }
    }
  } catch (error) {
    appendMessage("assistant", `Não foi possível concluir: ${error.message}`);
  } finally {
    activeMediaJobId = null;
    cancelRequested = false;
    setBusy(false);
    setActivity("");
    promptInput.focus();
  }
}

async function deleteConversation() {
  if (busy || activeConversationId === null) return;
  const title = conversationSelect.selectedOptions[0]?.textContent.replace(/\s+\(\d+\)$/, "") || "esta conversa";
  if (!window.confirm(`Excluir “${title}” deste computador?`)) return;
  setBusy(true, "Excluindo conversa local…");
  try {
    const response = await fetch(`/api/conversations/${activeConversationId}`, {
      method: "DELETE",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Não foi possível excluir a conversa.");
    activeConversationId = payload.conversation.id;
    await refreshConversationList(activeConversationId);
    await loadHistory(activeConversationId);
  } catch (error) {
    appendMessage("assistant", `Não foi possível excluir a conversa: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function switchConversation() {
  if (busy) return;
  const selectedId = Number(conversationSelect.value);
  if (!Number.isInteger(selectedId) || selectedId === activeConversationId) return;
  activeConversationId = selectedId;
  savePreferences();
  setBusy(true, "Carregando conversa local…");
  try {
    await loadHistory(activeConversationId);
  } finally {
    setBusy(false);
  }
}

function splitSpeechText(text, maxLength = 900) {
  const sentences = text.match(/[^.!?…]+[.!?…]+(?:\s+|$)|[^.!?…]+$/gu) || [text];
  const chunks = [];
  let current = "";
  for (const sentence of sentences) {
    let remaining = sentence.trim();
    while (remaining.length > maxLength) {
      let cut = remaining.lastIndexOf(" ", maxLength);
      if (cut < maxLength * 0.5) cut = maxLength;
      const part = remaining.slice(0, cut).trim();
      if (part) chunks.push(part);
      remaining = remaining.slice(cut).trim();
    }
    if (!remaining) continue;
    if (current && current.length + remaining.length + 1 > maxLength) {
      chunks.push(current);
      current = "";
    }
    current = current ? `${current} ${remaining}` : remaining;
  }
  if (current) chunks.push(current);
  return chunks;
}

async function speak(text, voiceId) {
  stopSpeech();
  const speech = {
    controller: new AbortController(),
    player: null,
    finishChunk: null,
    stopped: false,
    urls: new Set(),
  };
  activeSpeech = speech;
  setBusy(true, "Preparando leitura em partes com síntese local…");
  try {
    const chunks = splitSpeechText(text);
    for (let index = 0; index < chunks.length && !speech.stopped; index += 1) {
      setBusy(true, chunks.length > 1
        ? `Gerando trecho ${index + 1} de ${chunks.length} para leitura…`
        : "Gerando fala com síntese local…");
      const response = await fetch("/api/speech", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: chunks[index], voice_id: voiceId }),
        signal: speech.controller.signal,
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.error || "A síntese de voz falhou.");
      }
      const audioUrl = URL.createObjectURL(await response.blob());
      speech.urls.add(audioUrl);
      const player = new Audio(audioUrl);
      speech.player = player;
      player.playbackRate = Number(speechRate.value);
      player.preservesPitch = true;
      await new Promise((resolve, reject) => {
        let settled = false;
        const finish = (error = null) => {
          if (settled) return;
          settled = true;
          speech.finishChunk = null;
          if (error) reject(error);
          else resolve();
        };
        speech.finishChunk = () => finish();
        player.addEventListener("ended", () => finish(), { once: true });
        player.addEventListener(
          "error",
          () => finish(new Error("O navegador não conseguiu reproduzir o áudio.")),
          { once: true },
        );
        player.play().catch(finish);
      });
      URL.revokeObjectURL(audioUrl);
      speech.urls.delete(audioUrl);
      speech.player = null;
    }
    if (!speech.stopped) lastSpeech = { text, voiceId };
  } catch (error) {
    if (error.name !== "AbortError") throw error;
  } finally {
    for (const audioUrl of speech.urls) URL.revokeObjectURL(audioUrl);
    speech.urls.clear();
    if (activeSpeech === speech) {
      activeSpeech = null;
      setBusy(false);
    }
  }
}

function stopSpeech() {
  if (!activeSpeech) return;
  activeSpeech.stopped = true;
  activeSpeech.controller.abort();
  activeSpeech.player?.pause();
  activeSpeech.finishChunk?.();
  for (const audioUrl of activeSpeech.urls) URL.revokeObjectURL(audioUrl);
  activeSpeech.urls.clear();
}

function repeatLastSpeech() {
  if (!lastSpeech || busy) return;
  speak(lastSpeech.text, lastSpeech.voiceId).catch((error) => {
    appendMessage("assistant", `Não foi possível repetir a leitura: ${error.message}`);
  });
}

async function transcribeRecording(blob) {
  setBusy(true, "Transcrevendo o áudio com Whisper local…");
  try {
    const response = await fetch("/api/transcribe", {
      method: "POST",
      headers: {
        "Content-Type": blob.type || "audio/webm",
        "X-Audio-Filename": "microfone.webm",
      },
      body: blob,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Falha ao transcrever o áudio.");
    const transcript = payload.text?.trim();
    if (!transcript) throw new Error("Whisper não reconheceu fala no áudio.");
    setBusy(false);
    await sendMessage(transcript);
  } catch (error) {
    appendMessage("assistant", `Não foi possível processar o áudio: ${error.message}`);
    setBusy(false);
  }
}

async function startRecording() {
  if (busy) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    appendMessage("assistant", "Este navegador não oferece captura de áudio compatível.");
    return;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    recorder = new MediaRecorder(mediaStream, { mimeType });
    audioChunks = [];
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) audioChunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      clearTimeout(recordTimeout);
      mediaStream?.getTracks().forEach((track) => track.stop());
      mediaStream = null;
      recordButton.setAttribute("aria-pressed", "false");
      recordButton.lastElementChild.textContent = "Falar";
      const audio = new Blob(audioChunks, { type: recorder.mimeType || "audio/webm" });
      recorder = null;
      if (audio.size) await transcribeRecording(audio);
    }, { once: true });
    recorder.start();
    recordButton.setAttribute("aria-pressed", "true");
    recordButton.lastElementChild.textContent = "Parar";
    recordTimeout = setTimeout(() => stopRecording(), 120000);
  } catch (error) {
    mediaStream?.getTracks().forEach((track) => track.stop());
    mediaStream = null;
    appendMessage("assistant", `Não foi possível acessar o microfone: ${error.message}`);
  }
}

function stopRecording() {
  if (recorder?.state === "recording") recorder.stop();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(promptInput.value);
});

cancelJobButton.addEventListener("click", cancelMediaJob);

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

taskSelect.addEventListener("change", () => {
  routeHint.textContent = taskSelect.value === "code"
    ? "Perfil de programação e código"
    : "Perfil de texto e conversa";
  savePreferences();
});
modelSelect.addEventListener("change", savePreferences);
imageCheckbox.addEventListener("change", () => {
  if (imageCheckbox.checked) videoCheckbox.checked = false;
  savePreferences();
});
videoCheckbox.addEventListener("change", () => {
  if (videoCheckbox.checked) imageCheckbox.checked = false;
  savePreferences();
});
speakCheckbox.addEventListener("change", savePreferences);
speechVoiceSelect.addEventListener("change", () => {
  savePreferences();
  updateSpeechVoiceNotice();
});
speechRate.addEventListener("input", () => {
  speechRateValue.value = `${Number(speechRate.value).toFixed(2)}×`;
  speechRateValue.textContent = speechRateValue.value;
  if (activeSpeech?.player) activeSpeech.player.playbackRate = Number(speechRate.value);
  savePreferences();
});
repeatSpeechButton.addEventListener("click", repeatLastSpeech);
stopSpeechButton.addEventListener("click", stopSpeech);
clearHistoryButton.addEventListener("click", deleteConversation);
conversationSelect.addEventListener("change", switchConversation);
newConversationButton.addEventListener("click", createConversation);
renameConversationButton.addEventListener("click", renameConversation);
galleryToggleButton.addEventListener("click", toggleGallery);
galleryRefreshButton.addEventListener("click", loadGallery);
diagnosticsToggleButton.addEventListener("click", toggleDiagnostics);
diagnosticsRefreshButton.addEventListener("click", loadDiagnostics);
themeToggle.addEventListener("click", () => {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
  savePreferences();
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (!savedTheme) updateThemeControl();
});

recordButton.addEventListener("click", () => {
  if (recorder?.state === "recording") stopRecording();
  else startRecording();
});

restorePreferences();
speechRateValue.value = `${Number(speechRate.value).toFixed(2)}×`;
speechRateValue.textContent = speechRateValue.value;
Promise.all([loadModels(), loadSpeechVoices(), loadConversations()]).then(([, , historyLoaded]) => {
  if (preferencesWarning) setStatus("error", preferencesWarning);
  else if (!historyLoaded) setStatus("error", "Histórico local indisponível");
});
