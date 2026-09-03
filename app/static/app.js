const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const state = {
  project: null,
  sourceRef: "source",
  activeImageUrl: null,
  activeVersionId: null,
  activeMask: null,
  targetMask: null,
  protectedMasks: [],
  points: [],
  selectionBox: null,
  selectionStart: null,
  selecting: false,
  stageMode: "source",
  pointLabel: 1,
  operation: "fill",
  canvasImage: null,
  polling: null,
  health: null,
};

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const body = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body.detail || body || `HTTP ${response.status}`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.className = "toast", 2800);
}

function cache(url) { return `${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`; }

async function checkHealth() {
  try {
    const health = await api("/api/health");
    state.health = health;
    const items = [
      [health.simple_semantic_fill, "语义 Fill Anything"],
      [health.wavespeed_key_ready, "SAM3 · WaveSpeed"],
      [health.image_api_ready, `${health.image_model || "Image Edit"} · 原生蒙版`],
    ];
    $("#healthBadges").innerHTML = items.map(([ok, text]) => `<span class="badge ${ok ? "" : "warn"}"><i></i>${text}</span>`).join("");
  } catch (error) {
    $("#healthBadges").innerHTML = `<span class="badge warn"><i></i>后端未连接</span>`;
  }
}

async function loadProjects() {
  const projects = await api("/api/projects");
  const query = $("#projectSearch").value.trim().toLowerCase();
  const shown = projects.filter(item => item.name.toLowerCase().includes(query));
  $("#projectList").innerHTML = shown.length ? shown.map(item => `
    <article class="project-card ${state.project?.id === item.id ? "active" : ""}" data-project="${item.id}">
      <img src="${cache(item.thumbnail_url)}" alt=""><div><strong>${escapeHtml(item.name)}</strong>
      <small>${item.width}×${item.height} · ${item.versions} 个结果</small></div>
    </article>`).join("") : `<p class="quiet">还没有保存的项目。</p>`;
  $$("[data-project]").forEach(card => card.onclick = () => openProject(card.dataset.project));
}

async function openProject(projectId) {
  state.project = await api(`/api/projects/${projectId}`);
  state.sourceRef = "source";
  state.activeVersionId = null;
  const draft = state.project.edit_draft?.source_ref === "source" ? state.project.edit_draft : null;
  const remembered = state.project.masks.find(item => item.id === (draft?.target_mask_id || state.project.active_mask_id) && (item.source_ref || "source") === "source") || null;
  state.activeMask = remembered;
  state.targetMask = remembered;
  state.protectedMasks = [];
  state.points = [];
  clearSelection(false);
  syncSegmentInputMode();
  if (remembered) {
    const provider = remembered.provider?.provider || remembered.provider;
    $("#growthMode").value = provider === "manual-region-mask"
      ? String(remembered.provider?.recommended_growth_ratio ?? .15)
      : "0.35";
  }
  $("#projectTitle").textContent = state.project.name;
  $("#editControls").classList.remove("disabled");
  $("#actionControls").classList.toggle("disabled", !state.targetMask);
  await showImage(state.project.source_url, "原始素材");
  renderProject();
  loadProjects();
}

function renderProject() {
  const p = state.project;
  if (!p) return;
  $("#versionCount").textContent = `${p.versions.length} 个结果`;
  const cards = [`<article class="version-card source-card ${state.sourceRef === "source" ? "active" : ""}" data-source-ref="source"><img src="${cache(p.source_url)}"><span>原始素材</span></article>`];
  p.versions.forEach(version => cards.push(`<article class="version-card ${state.sourceRef === version.id ? "active" : ""}" data-source-ref="${version.id}" data-url="${version.url}"><img src="${cache(version.url)}"><span>${operationName(version.operation)} · ${shortId(version.id)}</span></article>`));
  $("#versionList").innerHTML = cards.join("");
  $$("[data-source-ref]").forEach(card => card.onclick = () => selectSource(card.dataset.sourceRef, card.dataset.url));

  $("#taskList").innerHTML = p.tasks.length ? p.tasks.map(taskCard).join("") : `<p class="quiet">还没有任务记录。</p>`;
  $$("[data-retry]").forEach(button => button.onclick = () => retryTask(button.dataset.retry));
  if (state.activeMask) {
    $("#maskSummary").className = "mask-summary ready";
    $("#maskSummary").textContent = `蒙版已就绪 · 覆盖画面 ${(state.activeMask.coverage * 100).toFixed(1)}%`;
  } else {
    $("#maskSummary").className = "mask-summary";
    $("#maskSummary").textContent = "尚未生成蒙版";
  }
  $("#maskRoleControls").classList.add("hidden");
  const layers = [];
  if (state.targetMask) layers.push(`修改目标：${escapeHtml(state.targetMask.prompt || shortId(state.targetMask.id))}`);
  if (state.protectedMasks.length) layers.push(`前景保护：${state.protectedMasks.map(item => escapeHtml(item.prompt || shortId(item.id))).join("、")}`);
  $("#layerSummary").classList.toggle("hidden", !layers.length);
  $("#layerSummary").innerHTML = layers.join("<br>");
  $("#clearProtection").classList.toggle("hidden", !state.protectedMasks.length);
  $("#occlusionSummary").classList.toggle("hidden", !state.targetMask);
  $("#occlusionSummary").innerHTML = state.targetMask
    ? `将修改 <b>${escapeHtml(state.targetMask.prompt || "已选目标")}</b>${state.protectedMasks.length ? `，并保持 <b>${state.protectedMasks.map(item => escapeHtml(item.prompt || "前景对象")).join("、")}</b> 原像素` : "；当前没有前景保护"}`
    : "";
}

function taskCard(task) {
  const mediaBase = `/media/projects/${task.project_id}/tasks/${task.id}`;
  const providerLink = task.artifacts?.provider_original ? `<a href="${mediaBase}/${task.artifacts.provider_original}" target="_blank">供应商原图</a>` : "";
  const candidateLink = task.artifacts?.layered_candidate_full ? `<a href="${mediaBase}/${task.artifacts.layered_candidate_full}" target="_blank">分层候选</a>` : "";
  const resultMaskLink = task.artifacts?.result_object_mask_preview ? `<a href="${mediaBase}/${task.artifacts.result_object_mask_preview}" target="_blank">新对象蒙版</a>` : "";
  const cleanPlateLink = task.artifacts?.clean_plate ? `<a href="${mediaBase}/${task.artifacts.clean_plate}" target="_blank">干净底板</a>` : "";
  const alphaLink = task.artifacts?.commit_alpha ? `<a href="${mediaBase}/${task.artifacts.commit_alpha}" target="_blank">软边 Alpha</a>` : "";
  const qualityLink = task.artifacts?.quality_report ? `<a href="${mediaBase}/${task.artifacts.quality_report}" target="_blank">质量报告</a>` : "";
  const resultLink = task.version_id ? `<a href="/api/projects/${task.project_id}/versions/${task.version_id}/download">下载结果</a>` : "";
  const retry = ["failed", "completed"].includes(task.status) ? `<button data-retry="${task.id}">重新执行</button>` : "";
  const detail = task.error ? friendlyError(task.error) : task.stage;
  const pipeline = task.pipeline_mode === "simple_fill" ? "Simple Fill" : task.pipeline_mode === "object_v2" ? "V2" : "Legacy";
  return `<article class="task-card"><header><b>${pipeline} · ${shortId(task.id)}</b><span class="status-${task.status}">${statusName(task.status)}</span></header><p>${escapeHtml(detail || "")}</p><footer>${resultLink}${cleanPlateLink}${candidateLink}${resultMaskLink}${alphaLink}${qualityLink}${providerLink}${retry}</footer></article>`;
}

async function selectSource(sourceRef, url) {
  state.sourceRef = sourceRef;
  state.activeVersionId = sourceRef === "source" ? null : sourceRef;
  state.activeMask = null;
  state.targetMask = null;
  state.protectedMasks = [];
  state.points = [];
  clearSelection(false);
  syncSegmentInputMode();
  $("#actionControls").classList.add("disabled");
  await showImage(url || state.project.source_url, sourceRef === "source" ? "原始素材" : `基于版本 ${shortId(sourceRef)} 继续`);
  renderProject();
  persistLayerDraft();
}

async function persistLayerDraft() {
  if (!state.project) return;
  try {
    await api(`/api/projects/${state.project.id}/edit-draft`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        source_ref: state.sourceRef,
        target_mask_id: state.targetMask?.id || null,
        protected_mask_ids: state.protectedMasks.map(item => item.id),
      }),
    });
  } catch (error) { toast(`编辑草稿保存失败：${error.message}`, true); }
}

async function showImage(url, label, resetMask = false, stageMode = "source") {
  state.activeImageUrl = url;
  state.stageMode = stageMode;
  const image = new Image();
  image.onload = () => {
    state.canvasImage = image;
    const canvas = $("#stageCanvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    canvas.getContext("2d").drawImage(image, 0, 0);
    drawPoints();
    canvas.classList.remove("hidden");
    $("#emptyState").classList.add("hidden");
    $("#stageNote").classList.remove("hidden");
    $("#sourceLabel").textContent = `${label} · ${image.naturalWidth}×${image.naturalHeight}`;
    $("#showSource").disabled = false;
    $("#fitCanvas").disabled = false;
    $("#downloadCurrent").classList.remove("disabled");
    $("#downloadCurrent").href = url;
    $("#downloadCurrent").setAttribute("download", "");
  };
  image.onerror = () => toast("图片加载失败", true);
  image.src = cache(url);
  if (resetMask) state.activeMask = null;
}

function drawPoints() {
  if (!state.canvasImage) return;
  const canvas = $("#stageCanvas");
  const ctx = canvas.getContext("2d");
  ctx.drawImage(state.canvasImage, 0, 0, canvas.width, canvas.height);
  const radius = Math.max(7, Math.min(canvas.width, canvas.height) * .012);
  state.points.forEach((point, index) => {
    ctx.beginPath(); ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = point.label ? "#11c690" : "#ef5148"; ctx.fill();
    ctx.lineWidth = Math.max(2, radius * .22); ctx.strokeStyle = "white"; ctx.stroke();
    ctx.fillStyle = "white"; ctx.font = `bold ${radius}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(index + 1), point.x, point.y + 1);
  });
  if (state.selectionBox) drawSelectionBox(ctx, canvas, state.selectionBox);
}

function drawSelectionBox(ctx, canvas, box) {
  const width = box.x_max - box.x_min;
  const height = box.y_max - box.y_min;
  if (width <= 0 || height <= 0) return;
  ctx.save();
  ctx.fillStyle = "rgba(17, 27, 23, .34)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(
    state.canvasImage,
    box.x_min, box.y_min, width, height,
    box.x_min, box.y_min, width, height,
  );
  const line = Math.max(3, Math.min(canvas.width, canvas.height) * .006);
  ctx.strokeStyle = "#16c693";
  ctx.lineWidth = line;
  ctx.setLineDash([line * 2.4, line * 1.4]);
  ctx.strokeRect(box.x_min, box.y_min, width, height);
  ctx.setLineDash([]);
  ctx.fillStyle = "#16c693";
  const handle = line * 1.8;
  [[box.x_min, box.y_min], [box.x_max, box.y_min], [box.x_min, box.y_max], [box.x_max, box.y_max]].forEach(([x, y]) => {
    ctx.fillRect(x - handle / 2, y - handle / 2, handle, handle);
  });
  ctx.restore();
}

function canvasPoint(event) {
  const canvas = $("#stageCanvas");
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.round((event.clientX - rect.left) * canvas.width / rect.width),
    y: Math.round((event.clientY - rect.top) * canvas.height / rect.height),
  };
}

function normalizedBox(a, b) {
  const canvas = $("#stageCanvas");
  return {
    x_min: Math.max(0, Math.min(canvas.width, Math.min(a.x, b.x))),
    y_min: Math.max(0, Math.min(canvas.height, Math.min(a.y, b.y))),
    x_max: Math.max(0, Math.min(canvas.width, Math.max(a.x, b.x))),
    y_max: Math.max(0, Math.min(canvas.height, Math.max(a.y, b.y))),
  };
}

function syncSelectionSummary() {
  const node = $("#selectionSummary");
  const button = $("#directRegionButton");
  const clear = $("#clearSelection");
  if (!state.selectionBox) {
    node.className = "selection-summary";
    node.textContent = "尚未框选 · 拖动图片即可开始";
    button.disabled = true;
    clear.classList.add("hidden");
    return;
  }
  const width = state.selectionBox.x_max - state.selectionBox.x_min;
  const height = state.selectionBox.y_max - state.selectionBox.y_min;
  node.className = "selection-summary ready";
  node.textContent = `已框选 ${width}×${height} px · 可直接使用或智能贴边`;
  button.disabled = width < 4 || height < 4;
  clear.classList.remove("hidden");
}

function clearSelection(redraw = true) {
  state.selectionBox = null;
  state.selectionStart = null;
  state.selecting = false;
  syncSelectionSummary();
  if (redraw) drawPoints();
}

function syncSegmentInputMode() {
  const input = $("#segmentPrompt");
  input.disabled = false;
  input.title = state.selectionBox ? "框选时，SAM3 会优先使用框的位置" : "";
  input.placeholder = state.selectionBox ? "可选：为这次框选加个名称" : "例如：书、花瓶、眼镜";
}

$("#stageCanvas").addEventListener("pointerdown", async event => {
  if (!state.project || !state.canvasImage || event.button !== 0) return;
  if (state.stageMode !== "source") {
    toast("已返回当前底图，请重新拖动框选");
    await returnToSelectedSource();
    return;
  }
  event.preventDefault();
  const canvas = $("#stageCanvas");
  canvas.setPointerCapture(event.pointerId);
  state.selectionStart = canvasPoint(event);
  state.selectionBox = normalizedBox(state.selectionStart, state.selectionStart);
  state.selecting = true;
  drawPoints();
});

$("#stageCanvas").addEventListener("pointermove", event => {
  if (!state.selecting || !state.selectionStart) return;
  event.preventDefault();
  state.selectionBox = normalizedBox(state.selectionStart, canvasPoint(event));
  drawPoints();
  syncSelectionSummary();
});

$("#stageCanvas").addEventListener("pointerup", event => {
  if (!state.selecting || !state.selectionStart) return;
  event.preventDefault();
  state.selectionBox = normalizedBox(state.selectionStart, canvasPoint(event));
  state.selecting = false;
  state.selectionStart = null;
  syncSelectionSummary();
  syncSegmentInputMode();
  drawPoints();
  if (!state.selectionBox || state.selectionBox.x_max - state.selectionBox.x_min < 4 || state.selectionBox.y_max - state.selectionBox.y_min < 4) {
    clearSelection();
    toast("框选太小，请按住拖动一个更大的区域", true);
  }
});

$("#stageCanvas").addEventListener("pointercancel", () => clearSelection());

$("#uploadButton").onclick = () => $("#fileInput").click();
$("#fileInput").onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  const data = new FormData();
  data.append("name", $("#projectName").value);
  data.append("image", file);
  $("#uploadButton").disabled = true;
  $("#uploadButton b").textContent = "正在写入项目…";
  try {
    const project = await api("/api/projects", {method: "POST", body: data});
    toast("项目已创建，原图已持久化保存");
    await openProject(project.id);
  } catch (error) { toast(error.message, true); }
  finally { $("#uploadButton").disabled = false; $("#uploadButton b").textContent = "上传原图"; event.target.value = ""; }
};

function selectedSourceUrl() {
  if (!state.project || state.sourceRef === "source") return state.project?.source_url;
  return state.project.versions.find(item => item.id === state.sourceRef)?.url;
}

async function returnToSelectedSource() {
  const url = selectedSourceUrl();
  if (url) await showImage(url, state.sourceRef === "source" ? "原始素材" : `基于版本 ${shortId(state.sourceRef)} 继续`);
}

$("#setTargetMask").onclick = async () => {
  if (!state.activeMask) return;
  state.targetMask = state.activeMask;
  state.protectedMasks = state.protectedMasks.filter(item => item.id !== state.activeMask.id);
  state.points = []; syncSegmentInputMode();
  $("#actionControls").classList.remove("disabled");
  renderProject(); await persistLayerDraft(); await returnToSelectedSource();
  toast("已设为修改目标；可继续选择手、袖口等前景保护");
};

$("#addProtectMask").onclick = async () => {
  if (!state.activeMask) return;
  if (state.targetMask?.id === state.activeMask.id) return toast("修改目标不能同时作为前景保护", true);
  if (!state.protectedMasks.some(item => item.id === state.activeMask.id)) state.protectedMasks.push(state.activeMask);
  state.points = []; syncSegmentInputMode();
  renderProject(); await persistLayerDraft(); await returnToSelectedSource();
  toast("已加入前景保护；可继续选择其他需要保持的对象");
};

$("#clearProtection").onclick = async () => { state.protectedMasks = []; renderProject(); await persistLayerDraft(); toast("已清空前景保护"); };

$$('[data-point-label]').forEach(button => button.onclick = () => {
  $$('[data-point-label]').forEach(item => item.classList.remove("active"));
  button.classList.add("active"); state.pointLabel = Number(button.dataset.pointLabel);
});
$("#clearSelection").onclick = () => { clearSelection(); syncSegmentInputMode(); };

async function acceptMask(mask, label, growthRatio) {
  state.activeMask = mask;
  state.targetMask = mask;
  state.protectedMasks = [];
  state.project = await api(`/api/projects/${state.project.id}`);
  state.canvasImage = null;
  $("#growthMode").value = String(growthRatio);
  await showImage(mask.preview_url, label, false, "mask-preview");
  $("#actionControls").classList.remove("disabled");
  renderProject();
  await persistLayerDraft();
}

$("#directRegionButton").onclick = async () => {
  if (!state.selectionBox) return toast("请先在图片上拖动框选", true);
  const button = $("#directRegionButton");
  button.disabled = true; button.textContent = "正在保存框选…";
  try {
    const mask = await api(`/api/projects/${state.project.id}/regions`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({box: state.selectionBox, label: $("#segmentPrompt").value.trim(), source_ref: state.sourceRef}),
    });
    await acceptMask(mask, "框选范围预览", .15);
    toast("已使用框选并预留安全生成边界；未调用 SAM3");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "直接使用框选"; }
};

$("#segmentButton").onclick = async () => {
  const prompt = $("#segmentPrompt").value.trim();
  if (!state.selectionBox && !prompt) return toast("请先拖动框选，或填写对象名称", true);
  const button = $("#segmentButton"); button.disabled = true; button.textContent = "SAM3 正在理解目标…";
  try {
    const mask = await api(`/api/projects/${state.project.id}/segment`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        boxes: state.selectionBox ? [state.selectionBox] : [],
        prompt: state.selectionBox ? "" : prompt,
        source_ref: state.sourceRef,
      }),
    });
    await acceptMask(mask, "SAM3 蒙版预览", .35);
    toast(state.selectionBox ? "SAM3 已在框内贴合对象边缘" : "SAM3 已按名称识别目标");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "SAM3 智能贴边"; }
};

$$('[data-operation]').forEach(button => button.onclick = () => {
  $$('[data-operation]').forEach(item => item.classList.remove("active"));
  button.classList.add("active"); state.operation = button.dataset.operation;
  const needsPrompt = state.operation !== "remove";
  $("#promptField").classList.toggle("hidden", !needsPrompt);
  $("#resultObjectField").classList.toggle("hidden", state.operation !== "fill");
  $("#generateProvider").textContent = "原生 mask 图像编辑";
  $("#generateButton span").textContent = state.operation === "remove" ? "移除选中内容" : state.operation === "fill" ? "重绘选中区域" : "保留主体并换背景";
});

$("#dilation").oninput = event => $("#dilationOutput").textContent = `${event.target.value} px`;
$("#feather").oninput = event => $("#featherOutput").textContent = `${event.target.value} px`;
$("#cleanupRadius").oninput = event => $("#cleanupRadiusOutput").textContent = `${event.target.value} px`;
$("#semanticEdge").oninput = event => $("#semanticEdgeOutput").textContent = `${event.target.value} px`;
$("#generateButton").onclick = async () => {
  if (!state.targetMask) return toast("请先生成蒙版并设为修改目标", true);
  const prompt = $("#generationPrompt").value.trim();
  if (state.operation !== "remove" && !prompt) return toast("请写一句希望生成的内容", true);
  try {
    const task = await api(`/api/projects/${state.project.id}/generate`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        operation: state.operation,
        mask_id: state.targetMask.id,
        prompt,
        dilation: Number($("#dilation").value),
        feather: Number($("#feather").value),
        protected_mask_ids: state.protectedMasks.map(item => item.id),
        result_object_prompt: state.operation === "fill" ? $("#resultObjectPrompt").value.trim() : "",
        pipeline_mode: "simple_fill",
        cleanup_radius: Number($("#cleanupRadius").value),
        semantic_edge: Number($("#semanticEdge").value),
        growth_ratio: Number($("#growthMode").value),
      }),
    });
    startPolling(task.id);
    toast("任务已启动；可以保留当前项目继续查看进度");
  } catch (error) { toast(error.message, true); }
};

async function startPolling(taskId) {
  clearInterval(state.polling);
  $("#taskProgress").classList.remove("hidden");
  const poll = async () => {
    try {
      const task = await api(`/api/projects/${state.project.id}/tasks/${taskId}`);
      $("#taskStage").textContent = task.stage; $("#taskPercent").textContent = `${task.progress}%`; $("#progressBar").style.width = `${task.progress}%`;
      if (["completed", "failed"].includes(task.status)) {
        clearInterval(state.polling); state.polling = null;
        state.project = await api(`/api/projects/${state.project.id}`);
        renderProject(); loadProjects();
        if (task.status === "completed") {
          const version = state.project.versions.find(item => item.id === task.version_id);
          await selectSource(version.id, version.url);
          toast("修改完成并已设为当前底图；现在可直接继续点选并运行 SAM3");
        } else { toast(`任务未完成：${friendlyError(task.error)}`, true); }
      }
    } catch (error) { clearInterval(state.polling); toast(error.message, true); }
  };
  await poll(); state.polling = setInterval(poll, 1800);
}

async function retryTask(taskId) {
  try {
    const task = await api(`/api/projects/${state.project.id}/tasks/${taskId}/retry`, {method: "POST"});
    startPolling(task.id); toast("已按原输入创建一次明确的新重试");
  } catch (error) { toast(error.message, true); }
}

$("#showSource").onclick = returnToSelectedSource;
$("#fitCanvas").onclick = () => $("#stageCanvas").scrollIntoView({block: "center", inline: "center"});
$("#refreshProjects").onclick = loadProjects;
$("#projectSearch").oninput = loadProjects;

function operationName(value) { return ({remove: "自然移除", fill: "区域重绘", replace_background: "换背景"})[value] || value; }
function statusName(value) { return ({created: "等待", generating: "生成中", completed: "已完成", failed: "未完成"})[value] || value; }
function shortId(value) { return value ? value.slice(-7) : ""; }
function friendlyError(value = "") {
  if (value.includes("503") || value.includes("providers_unavailable")) return "图像编辑服务暂时不可用；已保留全部输入，可直接重试。";
  if (value.toLowerCase().includes("timeout")) return "服务等待超时；任务资料已保留，可确认后重试。";
  if (value.toLowerCase().includes("cuda") && value.toLowerCase().includes("memory")) return "本机显存不足；原图与蒙版未丢失。";
  return value.length > 180 ? `${value.slice(0, 180)}…` : value;
}
function escapeHtml(value = "") { const node = document.createElement("div"); node.textContent = value; return node.innerHTML; }

checkHealth();
loadProjects().catch(error => toast(error.message, true));
