"use strict";

// Synthetic presentation-only data. This file has no fetch calls, storage, or
// provider connections; interactions never write project decisions.
const questions = {
  sources: {
    title: "Which source policy should research use?",
    priority: "Blocking decision",
    origin: "Asked 2 hours ago · source run run.research-04",
    impact: "This choice changes what evidence the research team can cite. Two downstream work items are paused until you decide.",
    affected: ["Map candidate system foundations", "Prepare comparison evidence"],
    options: [
      ["Prioritize peer-reviewed and official sources", "Higher confidence; narrower coverage and slower research."],
      ["Include reputable technical reporting", "Broader coverage; claims must be clearly labeled and cross-checked."],
    ],
  },
  scope: {
    title: "Should the UI include a global workspace view?",
    priority: "Non-blocking tradeoff",
    origin: "Asked 35 minutes ago · source run run.ui-review-02",
    impact: "This affects navigation and information density. No work is paused; the UI evaluation can continue either way.",
    affected: ["Compare project-first UI foundations"],
    options: [
      ["Keep the experience project-first", "Simpler privacy boundaries; users switch projects to see their work."],
      ["Add a cross-project overview", "More convenient; requires careful project access filtering."],
    ],
  },
};

const views = ["overview", "work", "conversation", "inbox", "agents", "evidence"];
let toastTimer;

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3400);
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
}

function makeAffectedRow(title, index) {
  const row = document.createElement("div");
  row.className = "affected-row";
  const dot = document.createElement("span");
  dot.className = `work-state ${index === 0 ? "state-waiting" : "state-blocked"}`;
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = title;
  row.append(dot, label);
  return row;
}

function makeOption(text, note, index) {
  const label = document.createElement("label");
  label.className = "option-card";
  const input = document.createElement("input");
  input.type = "radio";
  input.name = "source-policy";
  input.value = `option-${index + 1}`;
  input.checked = index === 0;
  const radio = document.createElement("span");
  radio.className = "option-radio";
  radio.setAttribute("aria-hidden", "true");
  const copy = document.createElement("span");
  const strong = document.createElement("strong");
  strong.textContent = text;
  const small = document.createElement("small");
  small.textContent = note;
  copy.append(strong, small);
  label.append(input, radio, copy);
  return label;
}

function showQuestion(key) {
  const question = questions[key];
  if (!question) return;
  document.querySelectorAll(".question-list-item").forEach((button) => {
    const selected = button.dataset.question === key;
    button.classList.toggle("chosen-question", selected);
    if (selected) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  });
  document.getElementById("question-detail-title").textContent = question.title;
  document.querySelector(".question-impact").textContent = question.impact;
  document.querySelector(".question-origin small").textContent = question.origin;
  const badge = document.querySelector(".question-detail-top .priority-badge");
  badge.lastChild.textContent = ` ${question.priority}`;

  const affected = document.querySelector(".affected-work");
  const affectedLabel = affected.querySelector(".detail-label");
  affected.replaceChildren(affectedLabel);
  affectedLabel.textContent = question.priority === "Blocking decision"
    ? `AFFECTED WORK · ${question.affected.length} PAUSED`
    : `RELATED WORK · ${question.affected.length} ITEM`;
  question.affected.forEach((title, index) => affected.append(makeAffectedRow(title, index)));

  const optionSet = document.querySelector(".option-set");
  const legend = optionSet.querySelector("legend");
  optionSet.replaceChildren(legend);
  question.options.forEach(([label, note], index) => optionSet.append(makeOption(label, note, index)));
  const persistenceNote = document.querySelector(".persistence-note");
  const noteIcon = document.createElement("span");
  noteIcon.setAttribute("aria-hidden", "true");
  noteIcon.textContent = "ⓘ";
  persistenceNote.replaceChildren(noteIcon, document.createTextNode(" Demo only. These controls do not save decisions or resume work."));
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
    document.getElementById(dialogControl.dataset.dialog).showModal();
    return;
  }

  const questionControl = event.target.closest("[data-question]");
  if (questionControl) {
    showQuestion(questionControl.dataset.question);
    return;
  }

  const inboxFilter = event.target.closest("[data-inbox-filter]");
  if (inboxFilter) {
    const resolved = inboxFilter.dataset.inboxFilter === "resolved";
    document.getElementById("open-inbox").hidden = resolved;
    document.getElementById("resolved-inbox").hidden = !resolved;
    document.querySelectorAll("[data-inbox-filter]").forEach((button) => {
      const selected = button === inboxFilter;
      button.classList.toggle("active-tab", selected);
      button.setAttribute("aria-selected", String(selected));
    });
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

document.getElementById("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  showToast("Demo only: no model is connected and this message was not sent or saved.");
});

document.querySelectorAll(".action-dialog form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (event.submitter?.value === "confirm") {
      // The button's data-toast listener explains that no command was executed.
      event.preventDefault();
      form.closest("dialog").close();
    }
  });
});
