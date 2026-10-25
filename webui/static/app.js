const state = {
  runs: [],
  jobs: [],
  selectedRunPath: "",
  currentRun: null,
  selectedStepIndex: 0,
  imageMode: "marked",
  activeTab: "candidates",
  polling: null,
  draftTask: false,
  followLatestStep: true,
  timelineAutoScrolling: false,
};

const INITIAL_STEP_BUDGET = 50;

const els = {
  runList: document.querySelector("#runList"),
  runSearch: document.querySelector("#runSearch"),
  refreshRuns: document.querySelector("#refreshRuns"),
  newTaskButton: document.querySelector("#newTaskButton"),
  newTaskPanel: document.querySelector("#newTaskPanel"),
  taskSettings: document.querySelector("#taskSettings"),
  taskSettingsToggle: document.querySelector("#taskSettingsToggle"),
  taskSettingsSummary: document.querySelector("#taskSettingsSummary"),
  unifiedComposerForm: document.querySelector("#unifiedComposerForm"),
  unifiedInput: document.querySelector("#unifiedInput"),
  sendComposerButton: document.querySelector("#sendComposerButton"),
  taskUrl: document.querySelector("#taskUrl"),
  taskProvider: document.querySelector("#taskProvider"),
  taskModel: document.querySelector("#taskModel"),
  taskBrowserBackend: document.querySelector("#taskBrowserBackend"),
  taskSteps: document.querySelector("#taskSteps"),
  taskHeadless: document.querySelector("#taskHeadless"),
  runTitle: document.querySelector("#runTitle"),
  runState: document.querySelector("#runState"),
  taskText: document.querySelector("#taskText"),
  answerText: document.querySelector("#answerText"),
  reportLink: document.querySelector("#reportLink"),
  reportRetryButton: document.querySelector("#reportRetryButton"),
  urlText: document.querySelector("#urlText"),
  providerText: document.querySelector("#providerText"),
  modelText: document.querySelector("#modelText"),
  browserText: document.querySelector("#browserText"),
  stepsText: document.querySelector("#stepsText"),
  durationText: document.querySelector("#durationText"),
  tracePath: document.querySelector("#tracePath"),
  stepTitle: document.querySelector("#stepTitle"),
  stepImage: document.querySelector("#stepImage"),
  emptyVisual: document.querySelector("#emptyVisual"),
  timeline: document.querySelector("#timeline"),
  jumpLatest: document.querySelector("#jumpLatest"),
  showMarked: document.querySelector("#showMarked"),
  showRaw: document.querySelector("#showRaw"),
  executionBadge: document.querySelector("#executionBadge"),
  actionJson: document.querySelector("#actionJson"),
  progressText: document.querySelector("#progressText"),
  verificationText: document.querySelector("#verificationText"),
  chatMessages: document.querySelector("#chatMessages"),
  approvePlanButton: document.querySelector("#approvePlanButton"),
  stopTaskButton: document.querySelector("#stopTaskButton"),
  tabBar: document.querySelector("#tabBar"),
  detailText: document.querySelector("#detailText"),
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadModelOptions();
  loadRuns();
  startPolling();
});

function bindEvents() {
  els.refreshRuns.addEventListener("click", () => loadRuns({ preserveSelection: true }));
  els.runSearch.addEventListener("input", renderRunList);
  els.taskSettingsToggle.addEventListener("click", () => toggleTaskSettings());
  els.taskProvider.addEventListener("change", () => loadModelOptions());
  els.taskModel.addEventListener("change", updateSettingsSummary);
  els.taskBrowserBackend.addEventListener("change", updateSettingsSummary);
  els.taskSteps.addEventListener("input", updateSettingsSummary);
  els.taskHeadless.addEventListener("change", updateSettingsSummary);
  els.newTaskButton.addEventListener("click", createBlankTask);
  els.unifiedComposerForm.addEventListener("submit", submitComposer);
  els.sendComposerButton.addEventListener("click", submitComposer);
  els.unifiedInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      submitComposer(event);
    }
  });
  els.approvePlanButton.addEventListener("click", () => approveCurrentPlan("用户点击同意执行。"));
  els.stopTaskButton.addEventListener("click", () => stopCurrentTask("用户点击停止。"));
  els.reportRetryButton.addEventListener("click", retryReportGeneration);
  els.jumpLatest.addEventListener("click", () => {
    if (!state.currentRun) return;
    state.followLatestStep = true;
    selectStep(Math.max(0, state.currentRun.steps.length - 1), { scrollIntoView: true });
  });
  els.timeline.addEventListener("scroll", handleTimelineScroll, { passive: true });
  els.showMarked.addEventListener("click", () => setImageMode("marked"));
  els.showRaw.addEventListener("click", () => setImageMode("raw"));
  els.tabBar.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-file]");
    if (!button) return;
    state.activeTab = button.dataset.file;
    [...els.tabBar.querySelectorAll("button")].forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    renderDetail();
  });
}

function createBlankTask() {
  state.draftTask = true;
  state.selectedRunPath = "";
  state.currentRun = null;
  state.selectedStepIndex = 0;
  state.followLatestStep = true;
  els.newTaskPanel.hidden = false;
  els.unifiedInput.value = "";
  els.taskUrl.value = "";
  toggleTaskSettings(false);
  renderRunList();
  renderEmptyState();
  els.unifiedInput.focus();
}

function toggleTaskSettings(forceOpen = null) {
  const open = forceOpen === null ? els.taskSettings.hidden : Boolean(forceOpen);
  els.taskSettings.hidden = !open;
  els.taskSettingsToggle.setAttribute("aria-expanded", String(open));
  els.taskSettingsToggle.textContent = open ? "收起设置" : "任务设置";
  updateSettingsSummary();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch {
      // Keep the HTTP message.
    }
    throw new Error(message);
  }
  return response.json();
}

async function loadModelOptions() {
  if (!els.taskModel) return;
  const provider = els.taskProvider.value || "gemini";
  const previous = els.taskModel.value;
  els.taskModel.disabled = true;
  try {
    const payload = await api(`/api/models?provider=${encodeURIComponent(provider)}`);
    const models = Array.isArray(payload.models) ? payload.models : [];
    const defaultModel = payload.default_model || models[0]?.id || previous || "";
    els.taskModel.replaceChildren();
    for (const item of models) {
      const modelId = String(item.id || item).trim();
      if (!modelId) continue;
      const option = document.createElement("option");
      option.value = modelId;
      option.textContent = modelId;
      option.selected = modelId === previous || (!previous && modelId === defaultModel);
      els.taskModel.append(option);
    }
    if (!els.taskModel.options.length && defaultModel) {
      const option = document.createElement("option");
      option.value = defaultModel;
      option.textContent = defaultModel;
      option.selected = true;
      els.taskModel.append(option);
    }
    if (![...els.taskModel.options].some((option) => option.selected) && els.taskModel.options.length) {
      const defaultOption = [...els.taskModel.options].find((option) => option.value === defaultModel);
      (defaultOption || els.taskModel.options[0]).selected = true;
    }
  } catch (error) {
    const fallback = previous || (provider === "deepseek" ? "deepseek-chat" : provider === "openai" ? "gpt-4o-mini" : "gemini-3.5-flash");
    els.taskModel.replaceChildren();
    const option = document.createElement("option");
    option.value = fallback;
    option.textContent = fallback;
    option.selected = true;
    els.taskModel.append(option);
  } finally {
    els.taskModel.disabled = false;
    updateSettingsSummary();
  }
}

function updateSettingsSummary() {
  if (!els.taskSettingsSummary) return;
  const providerLabel = els.taskProvider.options[els.taskProvider.selectedIndex]?.textContent || els.taskProvider.value || "Provider";
  const provider = providerLabel.replace(/\s*\(.+\)\s*$/, "");
  const model = els.taskModel.value || "model";
  const browserLabel = els.taskBrowserBackend.options[els.taskBrowserBackend.selectedIndex]?.textContent || els.taskBrowserBackend.value || "browser";
  const browser = browserLabel.replace(/\s*\(.+\)\s*$/, "");
  els.taskSettingsSummary.textContent = `${provider} · ${model} · ${browser}`;
}

async function loadRuns({ preserveSelection = false } = {}) {
  try {
    const payload = await api("/api/runs");
    state.runs = payload.runs || [];
    state.jobs = payload.jobs || [];
    renderRunList();

    if (state.draftTask) {
      renderEmptyState();
      return;
    }

    const items = combinedRunItems();
    const selectedExists = items.some((item) => item.path === state.selectedRunPath);
    if (!preserveSelection || !selectedExists) {
      const defaultItem =
        items.find((item) => ["needs_user_input", "waiting"].includes(item.status)) ||
        items.find((item) => item.job && ["queued", "planning", "awaiting_approval", "running"].includes(item.status)) ||
        items.find((item) => ["awaiting_approval", "queued", "planning", "running"].includes(item.status)) ||
        items.find((item) => item.status === "success" || item.success === true) ||
        items.find((item) => Number(item.steps || 0) > 0) ||
        items[0];
      if (defaultItem) {
        await selectRun(defaultItem.path, { keepStep: false });
      } else {
        renderEmptyState();
      }
    } else if (state.selectedRunPath) {
      await selectRun(state.selectedRunPath, { keepStep: !state.followLatestStep, quiet: true });
    }
  } catch (error) {
    showDetailError(error);
  }
}

function combinedRunItems() {
  const jobByPath = new Map(state.jobs.map((job) => [job.trace_path, job]));
  const items = state.runs.map((run) => {
    const job = jobByPath.get(run.path);
    return {
      ...run,
      status: job?.status || run.status,
      success: typeof job?.success === "boolean" ? job.success : run.success,
      instruction: run.instruction || job?.task || "",
      start_url: run.start_url || job?.url || "",
      provider: run.provider || job?.provider || "",
      model: run.model || job?.model || "",
      browser_backend: run.browser_backend || job?.browser_backend || "playwright",
      job,
    };
  });
  const knownPaths = new Set(items.map((item) => item.path));
  for (const job of state.jobs) {
    if (!knownPaths.has(job.trace_path)) {
      items.push({
        path: job.trace_path,
        group: "webui_runs",
        name: job.id,
        status: job.status,
        success: job.success,
        steps: 0,
        duration: 0,
        instruction: job.task,
        start_url: job.url,
        provider: job.provider,
        model: job.model || "",
        browser_backend: job.browser_backend || "playwright",
        started_at: job.started_at || job.created_at,
        job,
      });
    }
  }
  return items.sort((a, b) => {
    const left = String(a.started_at || "");
    const right = String(b.started_at || "");
    if (left === right) return String(b.name || "").localeCompare(String(a.name || ""));
    return left < right ? 1 : -1;
  });
}

function renderRunList() {
  const query = els.runSearch.value.trim().toLowerCase();
  const items = combinedRunItems().filter((item) => {
    if (!query) return true;
    return [
      item.name,
      item.group,
      item.status,
      item.instruction,
      item.provider,
      item.model,
      item.browser_backend,
      item.start_url,
    ]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  els.runList.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "还没有可显示的运行记录。";
    els.runList.append(empty);
    return;
  }

  for (const item of items) {
    const fullInstruction = item.instruction || item.name || "";
    const shortTitle = compactTaskTitle(fullInstruction, item.start_url);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "run-item";
    button.classList.toggle("active", item.path === state.selectedRunPath);
    button.addEventListener("click", () => {
      state.followLatestStep = true;
      selectRun(item.path, { keepStep: false });
    });

    const title = document.createElement("strong");
    title.textContent = shortTitle;
    title.title = fullInstruction;

    const subtitle = document.createElement("span");
    subtitle.textContent = `${item.group} / ${item.name}`;

    const meta = document.createElement("div");
    meta.className = "run-meta";
    const status = document.createElement("span");
    status.className = `status-chip ${statusClass(item.status, item.success)}`;
    status.textContent = statusLabel(item.status, item.success);
    const steps = document.createElement("span");
    steps.textContent = `${item.steps || 0} steps`;
    meta.append(status, steps);

    button.append(title, subtitle, meta);
    els.runList.append(button);
  }
}

async function selectRun(path, { keepStep = true, quiet = false } = {}) {
  state.draftTask = false;
  state.selectedRunPath = path;
  renderRunList();
  try {
    const payload = await api(`/api/run?path=${encodeURIComponent(path)}`);
    state.currentRun = payload;
    const latestIndex = Math.max(0, payload.steps.length - 1);
    const nextIndex = keepStep
      ? Math.min(state.selectedStepIndex, latestIndex)
      : latestIndex;
    state.selectedStepIndex = nextIndex;
    renderRun();
  } catch (error) {
    if (!quiet) showDetailError(error);
  }
}

function renderRun() {
  const run = state.currentRun;
  if (!run) {
    renderEmptyState();
    return;
  }

  const summary = run.summary || {};
  const config = run.config || {};
  const instruction = summary.instruction || config.instruction || "未命名任务";
  const compactTitle = compactTaskTitle(instruction, config.start_url || summary.final_url || "");
  const status = summary.final_status || "running";
  const success = typeof summary.success === "boolean" ? summary.success : null;
  const reportReady = hasGeneratedReport(run);

  els.runTitle.textContent = compactTitle;
  els.runTitle.title = instruction;
  els.runState.textContent = statusLabel(status, success);
  els.runState.className = `run-state ${statusClass(status, success)}`;
  els.taskText.textContent = instruction;
  const finalAnswer = finalAnswerText(run);
  els.answerText.textContent = compactAnswerPreview(finalAnswer);
  updateReportControls(run, reportReady);
  els.urlText.textContent = config.start_url || summary.final_url || "--";
  els.providerText.textContent = config.provider || "--";
  els.modelText.textContent = config.model || summary.model || "--";
  els.browserText.textContent = config.browser_backend || summary.browser_backend || "playwright";
  els.stepsText.textContent = `${run.steps.length} / ${config.max_steps || summary.steps_used || "--"}`;
  els.durationText.textContent = formatDuration(summary.duration_seconds);
  els.tracePath.textContent = run.path;
  renderChat(run);

  renderTimeline();
  if (run.steps.length) {
    selectStep(state.selectedStepIndex);
  } else {
    renderNoStep();
  }
}

function renderTimeline() {
  const run = state.currentRun;
  els.timeline.replaceChildren();
  if (!run?.steps?.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "等待 step_000 写入。";
    els.timeline.append(empty);
    return;
  }
  run.steps.forEach((step, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "timeline-step";
    button.classList.toggle("active", index === state.selectedStepIndex);
    button.addEventListener("click", () => selectStep(index, { manual: true }));

    const img = document.createElement("img");
    const imagePath = step.marked_screenshot || step.screenshot;
    if (imagePath) img.src = fileUrl(imagePath);

    const title = document.createElement("strong");
    title.textContent = step.name;
    const action = document.createElement("span");
    action.textContent = step.action?.action || "observing";

    button.append(img, title, action);
    els.timeline.append(button);
  });
}

function selectStep(index, { manual = false, scrollIntoView = null } = {}) {
  const run = state.currentRun;
  if (!run?.steps?.length) return;
  state.selectedStepIndex = Math.max(0, Math.min(index, run.steps.length - 1));
  if (manual) {
    state.followLatestStep = state.selectedStepIndex === run.steps.length - 1;
  }
  const shouldScrollIntoView = scrollIntoView ?? (manual || state.followLatestStep);
  const step = run.steps[state.selectedStepIndex];

  [...els.timeline.querySelectorAll(".timeline-step")].forEach((button, itemIndex) => {
    button.classList.toggle("active", itemIndex === state.selectedStepIndex);
    if (itemIndex === state.selectedStepIndex && shouldScrollIntoView) {
      keepTimelineCardVisible(button);
    }
  });

  els.stepTitle.textContent = `${step.name} · ${step.action?.action || "observe"}`;
  els.executionBadge.textContent = executionLabel(step.execution_ok);
  els.executionBadge.className = `status-chip ${executionClass(step.execution_ok)}`;
  els.actionJson.textContent = pretty(step.action || {});
  els.progressText.textContent = step.progress || "--";
  els.verificationText.textContent = step.verification || "--";
  renderImage();
  renderDetail();
}

function keepTimelineCardVisible(button) {
  const container = els.timeline;
  if (!button || !container) return;
  const left = button.offsetLeft;
  const right = left + button.offsetWidth;
  const viewportLeft = container.scrollLeft;
  const viewportRight = viewportLeft + container.clientWidth;
  if (left < viewportLeft) {
    state.timelineAutoScrolling = true;
    container.scrollTo({ left: Math.max(0, left - 8), behavior: "smooth" });
  } else if (right > viewportRight) {
    state.timelineAutoScrolling = true;
    container.scrollTo({ left: right - container.clientWidth + 8, behavior: "smooth" });
  }
  if (state.timelineAutoScrolling) {
    window.setTimeout(() => {
      state.timelineAutoScrolling = false;
    }, 450);
  }
}

function handleTimelineScroll() {
  const container = els.timeline;
  if (!container || state.timelineAutoScrolling) return;
  const nearRight = container.scrollLeft + container.clientWidth >= container.scrollWidth - 24;
  state.followLatestStep = nearRight;
}

function renderNoStep() {
  els.stepTitle.textContent = "等待第一步";
  els.stepImage.classList.remove("visible");
  els.stepImage.removeAttribute("src");
  els.emptyVisual.hidden = false;
  els.executionBadge.textContent = "--";
  els.executionBadge.className = "status-chip";
  els.actionJson.textContent = "{}";
  els.progressText.textContent = "--";
  els.verificationText.textContent = "--";
  els.detailText.textContent = "这个 run 还没有生成 step 文件。";
}

function renderChat(run) {
  els.chatMessages.replaceChildren();
  els.approvePlanButton.hidden = true;
  els.stopTaskButton.hidden = true;
  els.unifiedInput.placeholder = "输入任务、回复 Agent、补充信息，或修改执行计划...";
  if (!run) {
    appendChatMessage("system", "输入一个网页任务后，我会先规划要打开的网站和执行方式，等你同意后才开始浏览。");
    return;
  }

  const summary = run.summary || {};
  const config = run.config || {};
  const history = chatHistoryFromRun(run);
  if (history.length) {
    history.forEach((message) => appendChatMessage(message.role, message.text));
    if (summary.final_status === "planning") {
      appendChatMessage("agent", "我正在规划应该打开哪个真实网站，以及接下来怎么执行。");
      els.stopTaskButton.hidden = false;
      els.unifiedInput.placeholder = "可以随时补充提示，也可以点“停止”。";
    } else if (summary.final_status === "running") {
      appendChatMessage("agent", "我正在浏览网页，找到结果后会直接回复。");
      els.stopTaskButton.hidden = false;
      els.unifiedInput.placeholder = "任务运行中，可以随时补充提示；明确发送“停止”会叫停。";
    } else if (summary.final_status === "awaiting_approval") {
      els.approvePlanButton.hidden = false;
      els.unifiedInput.placeholder = "不同意就写修改意见；同意可以直接点按钮或输入“同意”。";
    } else if (summary.final_status === "needs_user_input") {
      els.unifiedInput.placeholder = "补充信息后发送，我会重新规划或继续任务...";
    }
    if (hasGeneratedReport(run) && isTerminalRun(summary.final_status, summary.success)) {
      appendReportMessage(run);
    }
    return;
  }

  const instruction = summary.instruction || config.instruction || "";
  if (instruction) appendChatMessage("user", instruction);

  if (summary.final_status === "needs_user_input") {
    appendChatMessage("agent", summary.final_answer || summary.final_reason || "我需要你补充一点信息。");
    els.unifiedInput.placeholder = "补充信息后发送，我会重新规划或继续任务...";
    return;
  }
  if (summary.final_status === "awaiting_approval") {
    appendChatMessage("agent", summary.final_answer || summary.final_reason || "我已经规划好执行方案，请确认是否开始。");
    els.approvePlanButton.hidden = false;
    els.unifiedInput.placeholder = "不同意就写修改意见；同意可以直接点按钮或输入“同意”。";
    return;
  }
  if (summary.success) {
    appendChatMessage("agent", "任务完成了，完整结果已经生成 HTML 报告。");
    appendReportMessage(run);
    return;
  }
  if (summary.final_status === "planning") {
    appendChatMessage("agent", "我正在规划应该打开哪个真实网站，以及接下来怎么执行。");
    els.stopTaskButton.hidden = false;
    return;
  }
  if (summary.final_status === "running") {
    appendChatMessage("agent", "我正在浏览网页，找到结果后会直接回复。");
    els.stopTaskButton.hidden = false;
    return;
  }
  if (summary.final_status && summary.final_status !== "not_started") {
    appendChatMessage("system", `运行结束：${statusLabel(summary.final_status, summary.success)}。${summary.final_reason || ""}`.trim());
    if (hasGeneratedReport(run)) {
      appendReportMessage(run);
    }
    return;
  }
  appendChatMessage("agent", "准备开始。");
}

function appendChatMessage(role, text) {
  const message = document.createElement("div");
  message.className = `chat-message ${normalizeChatRole(role)}`;
  message.textContent = text || "--";
  els.chatMessages.append(message);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function appendReportMessage(run) {
  const url = reportUrl(run);
  if (!url || !hasGeneratedReport(run)) return;
  const message = document.createElement("div");
  message.className = "chat-message agent report-message";
  const text = document.createElement("span");
  text.textContent = "建议在报告页查看排版版结果：";
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "打开 HTML 报告";
  message.append(text, link);
  els.chatMessages.append(message);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function chatHistoryFromRun(run) {
  const summary = run?.summary || {};
  const config = run?.config || {};
  const metadata = summary.task_metadata || config.task_metadata || {};
  const candidates = [summary.chat_history, metadata.chat_history, config.chat_history];
  for (const candidate of candidates) {
    if (!Array.isArray(candidate)) continue;
    const normalized = candidate
      .map((item) => ({
        role: normalizeChatRole(item?.role || "agent"),
        text: String(item?.text || "").trim(),
      }))
      .filter((item) => item.text);
    if (normalized.length) return normalized;
  }
  return [];
}

function chatHistoryForOutgoing(run, userText) {
  const history = chatHistoryFromRun(run).slice();
  const text = String(userText || "").trim();
  if (!text) return history;
  const last = history[history.length - 1];
  if (!last || last.role !== "user" || last.text !== text) {
    history.push({ role: "user", text });
  }
  return history;
}

function normalizeChatRole(role) {
  const normalized = String(role || "").toLowerCase();
  if (["user", "human"].includes(normalized)) return "user";
  if (["system", "status"].includes(normalized)) return "system";
  return "agent";
}

function renderImage() {
  const step = currentStep();
  if (!step) {
    renderNoStep();
    return;
  }
  const imagePath =
    state.imageMode === "raw"
      ? step.screenshot || step.marked_screenshot
      : step.marked_screenshot || step.screenshot;

  if (!imagePath) {
    els.stepImage.classList.remove("visible");
    els.stepImage.removeAttribute("src");
    els.emptyVisual.hidden = false;
    return;
  }

  els.emptyVisual.hidden = true;
  els.stepImage.src = fileUrl(imagePath);
  els.stepImage.classList.add("visible");
}

async function renderDetail() {
  const step = currentStep();
  if (!step) {
    els.detailText.textContent = "选择一个 step 查看观察、prompt 和执行结果。";
    return;
  }
  const filePath = step.files?.[state.activeTab];
  if (!filePath) {
    els.detailText.textContent = `这个 step 没有 ${state.activeTab} 文件。`;
    return;
  }
  els.detailText.textContent = "Loading...";
  try {
    const response = await fetch(`/api/text?path=${encodeURIComponent(filePath)}&t=${Date.now()}`);
    const text = await response.text();
    if (!response.ok) throw new Error(text || response.statusText);
    els.detailText.textContent = formatText(text);
  } catch (error) {
    els.detailText.textContent = `读取失败：${error.message}`;
  }
}

async function submitComposer(event) {
  event.preventDefault();
  const text = els.unifiedInput.value.trim();
  if (!text) return;

  const run = state.currentRun || {};
  const summary = run.summary || {};
  if (state.draftTask || !state.currentRun) {
    await startNewTask(text);
    return;
  }

  if (summary.final_status === "awaiting_approval" && looksLikeApproval(text)) {
    await approveCurrentPlan(text);
    els.unifiedInput.value = "";
    return;
  }
  if (looksLikeRejection(text)) {
    await stopCurrentTask(text);
    els.unifiedInput.value = "";
    return;
  }

  await continueCurrentTask(text);
}

async function startNewTask(text) {
  const submitButton = els.sendComposerButton;
  submitButton.disabled = true;
  submitButton.textContent = "规划中...";

  try {
    const payload = {
      url: els.taskUrl.value.trim(),
      task: text,
      provider: els.taskProvider.value || "gemini",
      model: els.taskModel.value || "",
      browser_backend: els.taskBrowserBackend.value || "playwright",
      max_steps: INITIAL_STEP_BUDGET,
      headless: els.taskHeadless.checked,
      chat_history: [{ role: "user", text }],
    };
    const result = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    els.unifiedInput.value = "";
    state.draftTask = false;
    state.jobs.unshift(result.job);
    await loadRuns({ preserveSelection: true });
    await selectRun(result.job.trace_path, { keepStep: false });
  } catch (error) {
    showDetailError(error);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "发送";
  }
}

async function continueCurrentTask(reply = "") {
  const jobId = currentJobId();
  if (!jobId) {
    showDetailError(new Error("当前没有可继续的任务。请先点“新建任务”。"));
    return;
  }
  const run = state.currentRun || {};
  const chatHistory = chatHistoryForOutgoing(run, reply);
  const submitButton = els.sendComposerButton;
  submitButton.disabled = true;
  submitButton.textContent = "继续中...";
  try {
    const result = await api("/api/tasks/continue", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, reply, chat_history: chatHistory }),
    });
    els.unifiedInput.value = "";
    const index = state.jobs.findIndex((job) => job.id === result.job.id);
    if (index >= 0) state.jobs[index] = result.job;
    else state.jobs.unshift(result.job);
    await loadRuns({ preserveSelection: true });
    await selectRun(result.job.trace_path, { keepStep: false });
  } catch (error) {
    showDetailError(error);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "发送";
  }
}

async function stopCurrentTask(reply = "") {
  const jobId = currentJobId();
  if (!jobId) {
    showDetailError(new Error("当前没有可停止的任务。"));
    return;
  }
  const submitButton = els.sendComposerButton;
  submitButton.disabled = true;
  submitButton.textContent = "停止中...";
  try {
    const chatHistory = chatHistoryForOutgoing(state.currentRun, reply || "停止，不继续。");
    const result = await api("/api/tasks/stop", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, reply, chat_history: chatHistory }),
    });
    const index = state.jobs.findIndex((job) => job.id === result.job.id);
    if (index >= 0) state.jobs[index] = result.job;
    else state.jobs.unshift(result.job);
    await loadRuns({ preserveSelection: true });
    await selectRun(result.job.trace_path, { keepStep: false });
  } catch (error) {
    showDetailError(error);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "发送";
  }
}

async function retryReportGeneration() {
  const jobId = currentJobId();
  if (!jobId) {
    showDetailError(new Error("当前没有可生成报告的任务。"));
    return;
  }
  els.reportRetryButton.disabled = true;
  els.reportRetryButton.textContent = "生成中...";
  try {
    const result = await api("/api/reports/retry", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId }),
    });
    const index = state.jobs.findIndex((job) => job.id === result.job.id);
    if (index >= 0) state.jobs[index] = result.job;
    else state.jobs.unshift(result.job);
    await loadRuns({ preserveSelection: true });
    await selectRun(result.job.trace_path, { keepStep: true });
  } catch (error) {
    showDetailError(error);
  } finally {
    els.reportRetryButton.disabled = false;
    els.reportRetryButton.textContent = "生成 HTML 报告";
  }
}

async function approveCurrentPlan(reply = "") {
  const jobId = currentJobId();
  if (!jobId) {
    showDetailError(new Error("当前没有可确认的任务。"));
    return;
  }
  els.approvePlanButton.disabled = true;
  els.approvePlanButton.textContent = "启动中...";
  try {
    const chatHistory = chatHistoryForOutgoing(state.currentRun, reply || "同意，开始执行。");
    const result = await api("/api/tasks/approve", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, approved: true, reply, chat_history: chatHistory }),
    });
    const index = state.jobs.findIndex((job) => job.id === result.job.id);
    if (index >= 0) state.jobs[index] = result.job;
    else state.jobs.unshift(result.job);
    await loadRuns({ preserveSelection: true });
    await selectRun(result.job.trace_path, { keepStep: false });
  } catch (error) {
    showDetailError(error);
  } finally {
    els.approvePlanButton.disabled = false;
    els.approvePlanButton.textContent = "同意，开始执行";
  }
}

function setImageMode(mode) {
  state.imageMode = mode;
  els.showMarked.classList.toggle("active", mode === "marked");
  els.showRaw.classList.toggle("active", mode === "raw");
  renderImage();
}

function startPolling() {
  if (state.polling) clearInterval(state.polling);
  state.polling = setInterval(() => {
    const hasRunningJob = state.jobs.some((job) => ["queued", "planning", "running"].includes(job.status));
    const runStatus = state.currentRun?.summary?.final_status;
    const reportStatus = state.currentRun?.summary?.report_status;
    if (hasRunningJob || runStatus === "planning" || runStatus === "running" || reportStatus === "queued" || reportStatus === "running") {
      loadRuns({ preserveSelection: true });
    }
  }, 1600);
}

function currentStep() {
  return state.currentRun?.steps?.[state.selectedStepIndex] || null;
}

function currentJobId() {
  const run = state.currentRun || {};
  const summary = run.summary || {};
  const config = run.config || {};
  if (summary.run_id) return summary.run_id;
  if (config.run_id) return config.run_id;
  const parts = String(run.path || "").split("/");
  return parts[parts.length - 1] || "";
}

function compactTaskTitle(instruction = "", startUrl = "") {
  const text = String(instruction || "").replace(/\s+/g, " ").trim();
  const url = String(startUrl || "").toLowerCase();
  const haystack = `${text.toLowerCase()} ${url}`;

  if (!text) return "未命名任务";
  if (haystack.includes("csrankings")) {
    if (haystack.includes("google scholar") || haystack.includes("h-index") || haystack.includes("学者") || haystack.includes("教授")) {
      return "CSRankings 学者检索";
    }
    return "CSRankings 排名筛选";
  }
  if (haystack.includes("天气") || haystack.includes("weather")) return "天气查询";
  if (haystack.includes("价格") || haystack.includes("比价") || haystack.includes("京东") || haystack.includes("淘宝") || haystack.includes("walmart") || haystack.includes("best buy")) {
    return "商品比价";
  }
  if (haystack.includes("arxiv") || haystack.includes("论文") || haystack.includes("paper")) return "论文检索";
  if (haystack.includes("github")) return "GitHub 信息检索";
  if (haystack.includes("官网") || haystack.includes("university") || haystack.includes("大学")) return "官网信息检索";

  const sentence = text.split(/[。.!！？?；;]/)[0] || text;
  const compact = sentence
    .replace(/^(请|帮我|你帮我|麻烦|去|在)\s*/u, "")
    .replace(/^(打开|查询|查找|搜索|找到)\s*/u, "");
  return truncateTitle(compact || text, 18);
}

function truncateTitle(text, maxChars) {
  const chars = Array.from(String(text || "").trim());
  if (chars.length <= maxChars) return chars.join("");
  return `${chars.slice(0, maxChars).join("")}…`;
}

function looksLikeApproval(text) {
  const normalized = text.trim().toLowerCase();
  if (looksLikeRejection(normalized)) {
    return false;
  }
  return ["同意", "可以", "确认", "开始", "执行", "approve", "approved", "yes", "ok", "go"].some((token) =>
    normalized.includes(token)
  );
}

function looksLikeRejection(text) {
  const normalized = text.trim().toLowerCase();
  return [
    "停止",
    "停吧",
    "停止任务",
    "取消任务",
    "结束任务",
    "不要继续",
    "不继续",
    "不同意",
    "stop",
    "cancel",
    "abort",
  ].some((token) => normalized === token || normalized.startsWith(`${token} `) || normalized.startsWith(`${token}，`) || normalized.startsWith(`${token},`));
}

function fileUrl(path) {
  return `/api/file?path=${encodeURIComponent(path)}&t=${Date.now()}`;
}

function pretty(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatText(text) {
  const trimmed = text.trim();
  if (!trimmed) return "";
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return text;
  }
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  if (!value) return "--";
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return `${minutes}m ${rest}s`;
}

function finalAnswerText(run) {
  const summary = run?.summary || {};
  if (summary.final_answer) return summary.final_answer;
  if (summary.final_status === "needs_user_input" && summary.final_reason) return summary.final_reason;
  if (summary.final_status === "awaiting_approval" && summary.final_reason) return summary.final_reason;
  if (summary.final_status === "success" && summary.final_reason) return summary.final_reason;
  const steps = run?.steps || [];
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const action = steps[index].action || {};
    if ((action.action === "answer" || action.action === "done") && action.text) {
      return action.text;
    }
  }
  if (["queued", "planning", "awaiting_approval", "running", "not_started"].includes(summary.final_status)) return "Agent is working...";
  return "--";
}

function compactAnswerPreview(text) {
  const value = String(text || "--").trim();
  if (value.length <= 520) return value;
  return `${value.slice(0, 520)}...`;
}

function reportUrl(run) {
  if (!run?.path) return "";
  return run.report_url || `/report?path=${encodeURIComponent(run.path)}`;
}

function updateReportControls(run, hasReport) {
  if (!els.reportLink || !els.reportRetryButton) return;
  const summary = run?.summary || {};
  const finalAnswer = finalAnswerText(run);
  const reportStatus = String(summary.report_status || "").toLowerCase();
  const url = hasReport ? reportUrl(run) : "";
  if (!url) {
    els.reportLink.hidden = true;
    els.reportLink.removeAttribute("href");
  } else {
    els.reportLink.href = url;
    els.reportLink.hidden = false;
  }

  const canGenerate = !hasReport && finalAnswer && finalAnswer !== "--" && !["queued", "planning", "awaiting_approval", "running", "not_started"].includes(summary.final_status);
  els.reportRetryButton.hidden = !canGenerate;
  els.reportRetryButton.disabled = reportStatus === "running" || reportStatus === "queued";
  if (reportStatus === "running" || reportStatus === "queued") {
    els.reportRetryButton.textContent = "HTML 报告生成中...";
  } else if (summary.final_report_error) {
    els.reportRetryButton.textContent = "重试生成 HTML 报告";
  } else {
    els.reportRetryButton.textContent = "生成 HTML 报告";
  }
}

function hasGeneratedReport(run) {
  return Boolean(run?.has_final_report || run?.final_report_path);
}

function isTerminalRun(status, success) {
  if (success === true || success === false) return !["queued", "planning", "awaiting_approval", "running", "not_started"].includes(status);
  return ["success", "fail", "user_stopped", "interrupted", "max_steps_exceeded", "stuck", "fatal_error", "step_error", "model_error", "done", "error"].includes(status);
}

function isReportReady(status, success) {
  if (success === true || status === "success") return true;
  return ["fail", "user_stopped", "interrupted", "max_steps_exceeded", "stuck", "fatal_error", "step_error", "model_error"].includes(status);
}
function statusLabel(status, success) {
  if (status === "queued") return "queued";
  if (status === "planning") return "planning";
  if (status === "awaiting_approval" || status === "waiting_approval") return "needs approval";
  if (status === "waiting" || status === "needs_user_input") return "waiting";
  if (status === "running") return "running";
  if (status === "done") return success ? "success" : "failed";
  if (status === "success") return "success";
  if (status === "fail") return "failed";
  if (status === "user_stopped") return "stopped";
  if (status === "max_steps_exceeded") return "max steps";
  if (status === "interrupted") return "interrupted";
  if (status === "stuck") return "stuck";
  if (status === "error" || status === "fatal_error" || status === "step_error" || status === "model_error") {
    return "error";
  }
  if (success === true) return "success";
  if (success === false) return "failed";
  return status || "unknown";
}

function statusClass(status, success) {
  if (status === "queued" || status === "planning" || status === "running" || status === "waiting" || status === "needs_user_input" || status === "awaiting_approval" || status === "waiting_approval") return "running";
  if (status === "success" || success === true) return "success";
  if (status === "fail" || status === "max_steps_exceeded" || status === "stuck" || status === "user_stopped" || success === false) return "warn";
  if (status === "error" || status === "fatal_error" || status === "step_error" || status === "model_error") {
    return "error";
  }
  return "";
}

function executionLabel(value) {
  if (value === true) return "ok";
  if (value === false) return "failed";
  return "--";
}

function executionClass(value) {
  if (value === true) return "ok";
  if (value === false) return "error";
  return "";
}

function renderEmptyState() {
  state.currentRun = null;
  state.selectedRunPath = "";
  els.runTitle.textContent = "选择一个任务或新建运行";
  els.runState.textContent = "idle";
  els.runState.className = "run-state";
  els.taskText.textContent = "--";
  els.answerText.textContent = "--";
  updateReportControls(null, false);
  els.urlText.textContent = "--";
  els.providerText.textContent = "--";
  els.modelText.textContent = "--";
  els.browserText.textContent = "--";
  els.stepsText.textContent = "--";
  els.durationText.textContent = "--";
  els.tracePath.textContent = "--";
  els.timeline.replaceChildren();
  renderChat(null);
  renderNoStep();
}

function showDetailError(error) {
  els.detailText.textContent = `错误：${error.message}`;
}



