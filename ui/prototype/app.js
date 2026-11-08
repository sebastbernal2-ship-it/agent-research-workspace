"use strict";

const PROJECT_ID = "project.northstar";
const PROJECT_API = `/api/projects/${encodeURIComponent(PROJECT_ID)}`;
const views = ["overview", "work", "conversation", "inbox", "agents", "evidence"];
let projectData = null;
let openQuestions = [];
let resolvedQuestions = [];
let selectedQuestion = null;
let currentInboxFilter = "open";
let serviceReady = false;
let toastTimer;

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3600);
}

async function apiRequest(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  const options = { method, headers, cache: "no-store", credentials: "omit" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-Local-Prototype"] = "1";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.message || "The local project service rejected the request.");
    error.status = response.status;
    error.code = payload.error;
    throw error;
  }
  return payload;
}

function setServiceState(ready, message) {
  serviceReady = ready;
  document.getElementById("service-status").textContent = message;
  document.getElementById("service-status").parentElement.classList.toggle("service-offline", !ready);
  document.querySelectorAll("[data-dialog='answer-dialog'], [data-dialog='reject-dialog']")
    .forEach((button) => { button.disabled = !ready; });
}

function showView(name) {
  if (!views.includes(name)) return;
  for (const view of views) {
    document.getElementById(`view-${view}`).hidden = view !== name;
  }
  document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
    const selected = button.dataset.view === name;
    button.classList.toggle("selected", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const title = document.querySelector(`#view-${name} h1`);
  if (title) {
    title.setAttribute("tabindex", "-1");
    title.focus({ preventScroll: true });
  }
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduceMotion ? "instant" : "smooth" });
  if (name === "inbox" && serviceReady) void loadInbox(currentInboxFilter);
  if (name === "work" && serviceReady) void loadProject();
}

function formatDate(value) {
  if (!value) return "No due date";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Date unavailable";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function formatTimestamp(value) {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

function statusPresentation(status) {
  const labels = {
    proposed: ["Proposed", "state-ready", "ready-label"],
    ready: ["Ready", "state-ready", "ready-label"],
    running: ["In progress", "state-running", "running-label"],
    waiting_human: ["Needs input", "state-waiting", "waiting-label"],
    blocked: ["Blocked", "state-blocked", "blocked-label"],
    verifying: ["Verifying", "state-running", "running-label"],
    completed: ["Complete", "state-complete", "complete-label"],
    failed: ["Failed", "state-blocked", "blocked-label"],
    cancelled: ["Cancelled", "state-blocked", "blocked-label"],
  };
  return labels[status] || [status, "state-ready", "ready-label"];
}

function createWorkRow(item, allWork, expanded = false) {
  const row = document.createElement("div");
  row.className = "work-row";
  const [statusLabel, dotClass, badgeClass] = statusPresentation(item.status);
  const dot = document.createElement("span");
  dot.className = `work-state ${dotClass}`;
  dot.setAttribute("role", "img");
  dot.setAttribute("aria-label", statusLabel);
  const copy = document.createElement("div");
  copy.className = "work-copy";
  const title = document.createElement("strong");
  title.textContent = item.title;
  const details = document.createElement("small");
  const dependencyNames = (item.dependsOn || []).map((id) => allWork.find((work) => work.workId === id)?.title || id);
  details.textContent = `${item.workType} · ${dependencyNames.length ? `depends on ${dependencyNames.join(", ")}` : "no dependencies"} · revision ${item.revision}`;
  copy.append(title, details);
  const status = document.createElement("span");
  status.className = `work-status ${badgeClass}`;
  status.textContent = statusLabel;
  row.append(dot, copy, status);
  if (expanded) {
    const more = document.createElement("button");
    more.className = "row-more";
    more.type = "button";
    more.textContent = "···";
    more.setAttribute("aria-label", `More options for ${item.title}`);
    more.disabled = true;
    row.append(more);
  }
  return row;
}

function renderWorkItems(items) {
  const overview = document.getElementById("overview-work-list");
  const fullList = document.getElementById("work-view-list");
  overview.replaceChildren();
  fullList.replaceChildren();
  items.slice(0, 4).forEach((item) => overview.append(createWorkRow(item, items)));
  items.forEach((item) => fullList.append(createWorkRow(item, items, true)));
  const counts = projectData.counts;
  document.getElementById("work-filter-all").textContent = String(counts.workItems);
  document.getElementById("work-filter-running").textContent = String(counts.runningWorkItems);
  document.getElementById("work-filter-blocked").textContent = String(counts.blockedWorkItems);
  document.getElementById("stat-work-count").textContent = String(counts.workItems);
  document.getElementById("stat-running-count").textContent = `${counts.runningWorkItems} in progress`;
  document.getElementById("stat-blocked-count").textContent = `${counts.blockedWorkItems} waiting`;
}

function renderRecentEvents(events) {
  const list = document.getElementById("activity-list");
  list.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("li");
    empty.className = "event-empty";
    empty.textContent = "No project events yet.";
    list.append(empty);
    return;
  }
  for (const event of events.slice(0, 4)) {
    const item = document.createElement("li");
    const avatar = document.createElement("span");
    avatar.className = `activity-icon ${event.actorLabel === "You" ? "icon-user" : event.actorLabel === "Project VP" ? "icon-vp" : "icon-agent"}`;
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = event.actorLabel === "You" ? "You" : event.actorLabel === "Project VP" ? "VP" : "A";
    const details = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${event.actorLabel} · ${event.title}`;
    const description = document.createElement("p");
    description.textContent = event.description;
    const time = document.createElement("time");
    time.dateTime = event.occurredAt;
    time.textContent = formatTimestamp(event.occurredAt);
    details.append(title, description, time);
    item.append(avatar, details);
    list.append(item);
  }
}

function makeQuestionListItem(question, selectedId) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `question-list-item${question.questionId === selectedId ? " chosen-question" : ""}`;
  button.dataset.questionId = question.questionId;
  button.setAttribute("role", "listitem");
  if (question.questionId === selectedId) button.setAttribute("aria-current", "true");
  const mark = document.createElement("span");
  mark.className = `question-list-mark ${question.priority === "high" || question.priority === "urgent" ? "high-mark" : "normal-mark"}`;
  mark.setAttribute("aria-hidden", "true");
  const copy = document.createElement("span");
  copy.className = "question-list-copy";
  const title = document.createElement("strong");
  title.textContent = question.questionText;
  const subtype = document.createElement("small");
  subtype.textContent = `${question.questionType} · ${question.blocking ? `blocks ${question.workIds.length} work item${question.workIds.length === 1 ? "" : "s"}` : "does not block work"}`;
  const metadata = document.createElement("span");
  metadata.className = "question-list-meta";
  const who = question.requestedBy?.role === "vp" ? "Project VP" : "Project agent";
  metadata.textContent = `${who} · ${question.dueAt ? `due ${formatDate(question.dueAt)}` : "no due date"}`;
  copy.append(title, subtype, metadata);
  const chevron = document.createElement("span");
  chevron.className = "question-chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "›";
  button.append(mark, copy, chevron);
  return button;
}

function renderQuestionList(items, selectedId = null) {
  const list = document.getElementById("question-list");
  list.replaceChildren();
  for (const question of items) list.append(makeQuestionListItem(question, selectedId));
}

function renderResolvedList(items) {
  const list = document.getElementById("resolved-list");
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "demo-caption";
    empty.textContent = "No resolved questions yet.";
    list.append(empty);
    return;
  }
  for (const question of items) {
    const row = document.createElement("div");
    row.className = "resolved-row";
    const title = document.createElement("strong");
    title.textContent = question.questionText;
    const state = document.createElement("span");
    state.className = "resolved-label";
    const when = question.closedAt || question.answer?.answeredAt || question.updatedAt || question.createdAt;
    state.textContent = `${question.status} · ${formatDate(when)}`;
    row.append(title, state);
    list.append(row);
  }
}

function makeAffectedWork(work) {
  const row = document.createElement("div");
  row.className = "affected-row";
  const [label, stateClass] = statusPresentation(work.status);
  const dot = document.createElement("span");
  dot.className = `work-state ${stateClass}`;
  dot.setAttribute("role", "img");
  dot.setAttribute("aria-label", label);
  const title = document.createElement("span");
  title.textContent = `${work.title} · ${label}`;
  row.append(dot, title);
  return row;
}

function makeQuestionOption(option, index) {
  const label = document.createElement("label");
  label.className = "option-card";
  const input = document.createElement("input");
  input.type = "radio";
  input.name = "question-option";
  input.value = option.optionId;
  input.checked = index === 0;
  const radio = document.createElement("span");
  radio.className = "option-radio";
  radio.setAttribute("aria-hidden", "true");
  const copy = document.createElement("span");
  const title = document.createElement("strong");
  title.textContent = option.label;
  const consequence = document.createElement("small");
  consequence.textContent = option.consequence || "No consequence details supplied.";
  copy.append(title, consequence);
  label.append(input, radio, copy);
  return label;
}

function renderQuestionDetail(detail) {
  selectedQuestion = detail;
  const record = detail.record;
  document.getElementById("question-detail-title").textContent = record.questionText;
  document.getElementById("question-impact").textContent = record.impact;
  document.getElementById("question-requested-by").textContent = record.requestedBy?.role === "vp" ? "Project VP" : "Project agent";
  document.getElementById("question-origin-meta").textContent = `Asked ${formatTimestamp(record.createdAt)}${record.runId ? ` · source run ${record.runId}` : " · no source run linked"}`;
  document.getElementById("question-priority").textContent = record.blocking ? "Blocking decision" : "Non-blocking question";
  document.querySelector(".priority-badge").classList.toggle("urgent-badge", record.blocking);

  const affected = document.getElementById("affected-work");
  const label = document.createElement("div");
  label.className = "detail-label";
  label.textContent = `${record.blocking ? "AFFECTED WORK" : "RELATED WORK"} · ${detail.affectedWork.length}${record.blocking ? " PAUSED" : " ITEMS"}`;
  affected.replaceChildren(label, ...detail.affectedWork.map(makeAffectedWork));

  const optionSet = document.getElementById("question-options");
  const legend = optionSet.querySelector("legend");
  optionSet.replaceChildren(legend);
  optionSet.hidden = record.options.length === 0;
  record.options.forEach((option, index) => optionSet.append(makeQuestionOption(option, index)));
  document.getElementById("answer-note").value = "";
  document.getElementById("reject-reason").value = "";
  const open = record.status === "open";
  document.querySelector("[data-dialog='answer-dialog']").disabled = !open || !serviceReady;
  document.querySelector("[data-dialog='reject-dialog']").disabled = !open || !serviceReady;
  document.querySelector(".reject-link").hidden = !open;
  document.querySelector(".answer-actions").hidden = !open;
  document.querySelector(".persistence-note").textContent = open
    ? "Each command is checked against this question and affected-work revision before it is saved."
    : `Resolved as ${record.status}. The canonical decision history is immutable.`;
}

async function loadQuestion(questionId) {
  try {
    const detail = await apiRequest(`${PROJECT_API}/inbox/${encodeURIComponent(questionId)}`);
    renderQuestionDetail(detail);
    renderQuestionList(openQuestions, questionId);
  } catch (error) {
    showToast(error.message);
  }
}

function setInboxFilter(status) {
  currentInboxFilter = status;
  const resolved = status === "resolved";
  document.getElementById("open-inbox").hidden = resolved || openQuestions.length === 0;
  document.getElementById("inbox-empty").hidden = resolved || openQuestions.length > 0;
  document.getElementById("resolved-inbox").hidden = !resolved;
  document.querySelectorAll("[data-inbox-filter]").forEach((button) => {
    const selected = button.dataset.inboxFilter === status;
    button.classList.toggle("active-tab", selected);
    button.setAttribute("aria-selected", String(selected));
  });
}

async function loadInbox(status = "open", preferredQuestionId = null) {
  if (!serviceReady) return;
  currentInboxFilter = status;
  try {
    const result = await apiRequest(`${PROJECT_API}/inbox?status=${encodeURIComponent(status)}`);
    if (status === "open") {
      openQuestions = result.questions;
      renderQuestionList(openQuestions);
      setInboxFilter("open");
      if (!openQuestions.length) {
        selectedQuestion = null;
        document.getElementById("inbox-preview-question").textContent = "No open questions";
        document.getElementById("inbox-preview-meta").textContent = "The project inbox is clear.";
        document.getElementById("inbox-preview-due").textContent = "";
        return;
      }
      const next = openQuestions.find((question) => question.questionId === preferredQuestionId)
        || openQuestions.find((question) => question.questionId === selectedQuestion?.questionId)
        || openQuestions[0];
      await loadQuestion(next.questionId);
      const preview = openQuestions[0];
      document.getElementById("inbox-preview-question").textContent = preview.questionText;
      document.getElementById("inbox-preview-meta").textContent = `${preview.blocking ? "Blocking" : "Non-blocking"} · ${preview.questionType} · requested by ${preview.requestedBy?.role === "vp" ? "Project VP" : "Project agent"}`;
      document.getElementById("inbox-preview-due").textContent = preview.dueAt ? `Due ${formatDate(preview.dueAt)}` : "";
      if (projectData) {
        document.getElementById("stat-question-count").textContent = String(openQuestions.length);
        document.getElementById("inbox-heading-count").textContent = String(openQuestions.length);
        document.getElementById("inbox-open-count").textContent = String(openQuestions.length);
        document.getElementById("sidebar-inbox-count").textContent = String(openQuestions.length);
        document.getElementById("inbox-preview-count").textContent = `${openQuestions.length} open`;
        document.querySelectorAll(".button-count").forEach((badge) => { badge.textContent = String(openQuestions.length); });
      }
    } else {
      resolvedQuestions = result.questions;
      document.getElementById("inbox-resolved-count").textContent = String(resolvedQuestions.length);
      renderResolvedList(resolvedQuestions);
      setInboxFilter("resolved");
    }
  } catch (error) {
    showToast(error.message);
    if (error.status === 403 || error.status === 404) setServiceState(false, "Local project service access failed · check the demo server.");
  }
}

async function loadProject() {
  if (!serviceReady) return;
  try {
    const data = await apiRequest(PROJECT_API);
    renderProject(data);
  } catch (error) {
    showToast(error.message);
    setServiceState(false, "Local project service unavailable · restart the demo server.");
  }
}

function renderProject(data) {
  projectData = data;
  const project = data.project;
  const counts = data.counts;
  document.getElementById("overview-title").textContent = project.name;
  document.getElementById("project-breadcrumb").textContent = project.name;
  document.getElementById("sidebar-project-name").textContent = project.name;
  document.getElementById("project-inbox-name").textContent = project.name;
  document.querySelector(".chat-context strong").textContent = `${project.name} project context`;
  document.querySelector(".context-panel h2").textContent = project.name;
  document.getElementById("project-charter-description").textContent = project.charter.goals.join(" ");
  document.getElementById("stat-run-count").textContent = String(counts.agentRuns);
  document.getElementById("stat-evidence-count").textContent = String(counts.evidence + counts.artifacts);
  document.getElementById("stat-question-count").textContent = String(counts.openQuestions);
  document.querySelector(".inbox-stat").setAttribute("aria-label", `${counts.openQuestions} open inbox questions`);
  document.querySelectorAll(".button-count").forEach((badge) => { badge.textContent = String(counts.openQuestions); });
  document.getElementById("inbox-heading-count").textContent = String(counts.openQuestions);
  document.getElementById("inbox-open-count").textContent = String(counts.openQuestions);
  document.getElementById("inbox-resolved-count").textContent = String(counts.resolvedQuestions);
  document.getElementById("sidebar-inbox-count").textContent = String(counts.openQuestions);
  document.getElementById("inbox-preview-count").textContent = `${counts.openQuestions} open`;
  document.querySelector(".charter-copy .muted").textContent = `· revision ${project.charter.revision}`;
  document.querySelector(".charter-detail .card-kicker").textContent = `PROJECT CHARTER · REVISION ${project.charter.revision}`;
  renderCharter(project.charter);
  renderWorkItems(data.workItems);
  renderRecentEvents(data.recentEvents);
}

function renderCharter(charter) {
  const columns = document.querySelectorAll(".charter-columns ul");
  for (const [list, values] of [[columns[0], charter.goals], [columns[1], charter.nonGoals]]) {
    list.replaceChildren();
    for (const value of values) {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    }
  }
}

async function submitAnswer() {
  if (!selectedQuestion || !serviceReady) return;
  const selectedOption = document.querySelector("input[name='question-option']:checked");
  const note = document.getElementById("answer-note").value.trim();
  const record = selectedQuestion.record;
  const option = record.options.find((entry) => entry.optionId === selectedOption?.value);
  const answerText = [option?.label, note].filter(Boolean).join("\n\nUser note: ");
  if (!answerText) {
    showToast("Select an option or add a written answer before confirming.");
    document.getElementById("answer-note").focus();
    return;
  }
  await runQuestionCommand("answer-dialog", async () => {
    const body = {
      expectedEtag: selectedQuestion.etag,
      text: answerText,
      ...(option ? { selectedOptionId: option.optionId } : {}),
    };
    const result = await apiRequest(`${PROJECT_API}/inbox/${encodeURIComponent(record.questionId)}/answers`, { method: "POST", body });
    const resumed = result.record.answer?.resumedWorkIds || [];
    return resumed.length
      ? `Decision saved. ${resumed.length} exact affected work item(s) returned to ready.`
      : "Answer saved to the canonical project history.";
  });
}

async function submitRejection() {
  if (!selectedQuestion || !serviceReady) return;
  const reason = document.getElementById("reject-reason").value.trim();
  if (!reason) {
    showToast("A reason is required to reject a proposal.");
    document.getElementById("reject-reason").focus();
    return;
  }
  await runQuestionCommand("reject-dialog", async () => {
    await apiRequest(`${PROJECT_API}/inbox/${encodeURIComponent(selectedQuestion.record.questionId)}/closure`, {
      method: "POST",
      body: { expectedEtag: selectedQuestion.etag, newStatus: "rejected", reason },
    });
    return "Proposal rejected. Any blocking work remains blocked.";
  });
}

async function runQuestionCommand(dialogId, command) {
  const dialog = document.getElementById(dialogId);
  const submitButton = dialog.querySelector("button[value='confirm']");
  submitButton.disabled = true;
  try {
    const message = await command();
    dialog.close();
    await loadProject();
    await loadInbox("open");
    showToast(message);
  } catch (error) {
    showToast(error.message);
    if (error.status === 409) {
      await loadProject();
      await loadInbox("open", selectedQuestion?.record.questionId);
    }
  } finally {
    submitButton.disabled = false;
  }
}

async function bootstrap() {
  try {
    const health = await apiRequest("/api/health");
    if (health.mode !== "local-synthetic-demo") throw new Error("Unexpected service mode.");
    setServiceState(true, "Local synthetic project · inbox persists to SQLite · model providers disconnected");
    await loadProject();
    await loadInbox("open");
  } catch (_error) {
    setServiceState(false, "UI shell only · start the local demo service; no project actions are available");
    document.getElementById("inbox-preview-question").textContent = "Local project service is not running";
    document.getElementById("inbox-preview-meta").textContent = "Start it with python3 -m ui.prototype.server from the repository root.";
  }
}

document.addEventListener("click", (event) => {
  const viewControl = event.target.closest("[data-view]");
  if (viewControl) {
    event.preventDefault();
    showView(viewControl.dataset.view);
    return;
  }
  const dialogControl = event.target.closest("[data-dialog]");
  if (dialogControl) {
    if (!serviceReady || dialogControl.disabled) {
      showToast("Start the loopback-only local project service to use inbox decisions.");
      return;
    }
    const dialogId = dialogControl.dataset.dialog;
    document.getElementById(dialogId).showModal();
    return;
  }
  const questionControl = event.target.closest("[data-question-id]");
  if (questionControl) {
    void loadQuestion(questionControl.dataset.questionId);
    return;
  }
  const inboxFilter = event.target.closest("[data-inbox-filter]");
  if (inboxFilter) {
    void loadInbox(inboxFilter.dataset.inboxFilter);
    return;
  }
  const toastControl = event.target.closest("[data-toast]");
  if (toastControl) showToast(toastControl.dataset.toast);
});

document.querySelector(".inbox-stat").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    showView("inbox");
  }
});

document.querySelectorAll("#answer-dialog form, #reject-dialog form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (event.submitter?.value !== "confirm") return;
    event.preventDefault();
    if (form.closest("dialog").id === "answer-dialog") void submitAnswer();
    else void submitRejection();
  });
});

bootstrap();
