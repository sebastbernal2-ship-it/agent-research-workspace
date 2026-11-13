import React from "react";
import { createRoot } from "react-dom/client";
import {
  ChevronDown, CircleHelp, Folder, FolderKanban, Inbox,
  Layers3, MessageSquare, Plus, Search, ShieldAlert, Sparkles, X,
} from "lucide-react";
import "./style.css";
import "./goal.css";
import "./work-views.css";
import "./kanban-board.css";

type Project = { projectId: string; name: string; status: string; charter: { revision: number; goals: string[]; nonGoals: string[]; acceptanceCriteria: string[] } };
type Work = { workId: string; title: string; objective: string; workType: string; status: string; parentWorkId?: string | null; dependsOn: string[]; acceptanceCriteria: string[]; revision: number };
type ProjectEvent = { eventId: string; title: string; description: string; actorLabel: string; occurredAt: string };
type AlignmentSegment = { index: number; title: string; prompt: string; evidence: string; status: string; answer: { text: string } | null };
type Alignment = { status: string; repositoryName: string; branch: string; head: string; segments: AlignmentSegment[] };
type Summary = { project: Project; workItems: Work[]; recentEvents: ProjectEvent[]; counts: Record<string, number>; alignment?: Alignment | null };
type RepoReport = { repositoryName: string; repositoryPath: string; head: string; branch: string; trackedFileCount: number; trackedChanges: number | null; topLevelAreas: string[]; documents: { path: string; title: string; excerpt: string }[]; goalHint: string; fingerprint: string; inspectionPolicy: string };
type ProjectMode = "choose" | "new" | "existing";
type Question = { questionId: string; questionType: string; questionText: string; priority: string; blocking: boolean; workIds: string[]; etag: string };
type QuestionDetail = Question & { record: { questionType: string; options: { optionId: string; label: string; consequence?: string }[]; impact: string; status: string; questionText: string }; affectedWork: { workId: string; title: string; status: string }[] };
type View = "overview" | "board" | "chat" | "inbox";

const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json", "X-Local-Prototype": "1" } : {}), ...init?.headers },
    credentials: "omit", cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "The local project service rejected the request.");
  return payload as T;
};

function App() {
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [projectId, setProjectId] = React.useState(() => new URLSearchParams(window.location.search).get("project") || "project.northstar");
  const [summary, setSummary] = React.useState<Summary | null>(null);
  const [view, setView] = React.useState<View>("overview");
  const [projectsOpen, setProjectsOpen] = React.useState(false);
  const [questions, setQuestions] = React.useState<Question[]>([]);
  const [selectedQuestion, setSelectedQuestion] = React.useState<QuestionDetail | null>(null);
  const [selectedOption, setSelectedOption] = React.useState("");
  const [answerNote, setAnswerNote] = React.useState("");
  const [decisionBusy, setDecisionBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [projectDialog, setProjectDialog] = React.useState(false);
  const [projectMode, setProjectMode] = React.useState<ProjectMode>("choose");
  const [repositoryPath, setRepositoryPath] = React.useState("");
  const [repoReport, setRepoReport] = React.useState<RepoReport | null>(null);
  const [draftName, setDraftName] = React.useState("");
  const [draftGoal, setDraftGoal] = React.useState("");
  const [inspectionBusy, setInspectionBusy] = React.useState(false);
  const [workDialog, setWorkDialog] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const loadProjects = React.useCallback(async () => {
    const result = await request<{items: Project[]}>('/api/v1/projects');
    setProjects(result.items);
    return result.items;
  }, []);

  const loadInbox = React.useCallback(async (id: string) => {
    await request(`/api/v1/projects/${encodeURIComponent(id)}/alignment/resume`, { method: "POST", body: JSON.stringify({}) });
    const result = await request<{questions: Question[]}>(`/api/v1/projects/${encodeURIComponent(id)}/inbox?status=open`);
    setQuestions(result.questions);
    if (!result.questions.length) { setSelectedQuestion(null); return result.questions; }
    const detail = await request<QuestionDetail>(`/api/v1/projects/${encodeURIComponent(id)}/inbox/${encodeURIComponent(result.questions[0].questionId)}`);
    setSelectedQuestion(detail);
    setSelectedOption(detail.record.options[0]?.optionId || "");
    setAnswerNote("");
    return result.questions;
  }, []);

  const loadProject = React.useCallback(async (id = projectId) => {
    const result = await request<Summary>(`/api/v1/projects/${encodeURIComponent(id)}`);
    setProjectId(id);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?project=${encodeURIComponent(id)}`);
    setSummary(result);
    setError("");
  }, [projectId]);

  React.useEffect(() => {
    void (async () => {
      try {
        await request('/api/v1/health');
        const items = await loadProjects();
        const preferred = items.find((item) => item.projectId === projectId) || items[0];
        if (preferred) { await loadProject(preferred.projectId); await loadInbox(preferred.projectId); }
      } catch (e) { setError(e instanceof Error ? e.message : "Local service unavailable."); }
    })();
  }, []);

  const project = summary?.project;
  const workItems = summary?.workItems || [];
  const openQuestionCount = summary?.counts.openQuestions || 0;
  const blockingQuestionCount = questions.filter((question) => question.blocking).length;
  const alignmentQuestionCount = questions.filter((question) => question.questionType === "clarification").length;
  const alignmentWork = workItems.find((item) => item.workId === "work.alignment-intake");
  const alignmentCompleted = alignmentWork?.status === "completed";
  const alignmentPending = alignmentQuestionCount > 0 || Boolean(alignmentWork && !["completed", "cancelled"].includes(alignmentWork.status));
  const breakdownItems = workItems.filter((item) => item.workId !== "work.alignment-intake" && item.status !== "cancelled");
  const runningWorkCount = workItems.filter((item) => ["running", "verifying"].includes(item.status)).length;
  const pulse = projectPulse(workItems.length, openQuestionCount, blockingQuestionCount, alignmentQuestionCount, alignmentWork, breakdownItems.length, runningWorkCount, alignmentCompleted);

  function openProjectDialog() {
    setProjectMode("choose"); setRepositoryPath(""); setRepoReport(null); setDraftName(""); setDraftGoal(""); setProjectDialog(true);
  }

  async function inspectRepository() {
    if (!repositoryPath.trim()) { setError("Choose a local Git repository path first."); return; }
    setInspectionBusy(true); setError("");
    try {
      const result = await request<{report: RepoReport}>("/api/v1/repositories/inspect", { method: "POST", body: JSON.stringify({ repositoryPath: repositoryPath.trim() }) });
      setRepoReport(result.report); setDraftName(result.report.repositoryName);
      setDraftGoal(result.report.goalHint || "");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not inspect the repository."); setRepoReport(null); }
    finally { setInspectionBusy(false); }
  }

  async function createProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    try {
      const body = projectMode === "existing"
        ? { mode: "existing", name: draftName, goal: draftGoal, repositoryPath: repoReport?.repositoryPath, inspectionFingerprint: repoReport?.fingerprint }
        : { mode: "new", name: draftName, goal: draftGoal };
      const result = await request<{project: Project}>('/api/v1/projects', { method: 'POST', body: JSON.stringify(body) });
      await loadProjects(); await loadProject(result.project.projectId); await loadInbox(result.project.projectId);
      setView(projectMode === "existing" ? "inbox" : "overview"); setProjectDialog(false);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create project."); }
    finally { setBusy(false); }
  }

  async function decideWork(workId: string, revision: number, decision: "approve" | "reject"): Promise<boolean> {
    if (!project) return false;
    setBusy(true);
    try {
      await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/work/${encodeURIComponent(workId)}/approval`, { method: "POST", body: JSON.stringify({ expectedRevision: revision, decision }) });
      await loadProject(project.projectId);
      return true;
    } catch (e) { setError(e instanceof Error ? e.message : "Could not record the work approval."); return false; }
    finally { setBusy(false); }
  }

  async function createWork(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!project) return; setBusy(true);
    const form = event.currentTarget; const data = new FormData(form);
    try {
      const acceptanceCriteria = String(data.get('acceptanceCriteria') || "").split("\n").map((line) => line.trim()).filter(Boolean);
      await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/work`, { method: 'POST', body: JSON.stringify({ title: data.get('title'), objective: data.get('objective'), workType: data.get('workType'), parentWorkId: data.get('parentWorkId') || null, acceptanceCriteria }) });
      await loadProject(project.projectId); setView("overview"); setWorkDialog(false); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not add planned work."); }
    finally { setBusy(false); }
  }

  async function chooseQuestion(questionId: string) {
    if (!project) return;
    try {
      const detail = await request<QuestionDetail>(`/api/v1/projects/${encodeURIComponent(project.projectId)}/inbox/${encodeURIComponent(questionId)}`);
      setSelectedQuestion(detail); setSelectedOption(detail.record.options[0]?.optionId || ""); setAnswerNote("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load question."); }
  }

  async function openQuestion(questionId: string) {
    await chooseQuestion(questionId);
    setView("inbox");
  }

  async function submitDecision(reject = false) {
    if (!project || !selectedQuestion) return;
    setDecisionBusy(true);
    try {
      if (reject) {
        if (!answerNote.trim()) throw new Error("Add a reason before rejecting this question.");
        await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/inbox/${encodeURIComponent(selectedQuestion.questionId)}/closure`, { method: "POST", body: JSON.stringify({ expectedEtag: selectedQuestion.etag, newStatus: "rejected", reason: answerNote.trim() }) });
      } else {
        const option = selectedQuestion.record.options.find((item) => item.optionId === selectedOption);
        const text = [option?.label, answerNote.trim()].filter(Boolean).join("\n\nNote: ");
        if (!text) throw new Error("Choose an option or add a written response.");
        await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/inbox/${encodeURIComponent(selectedQuestion.questionId)}/answers`, { method: "POST", body: JSON.stringify({ expectedEtag: selectedQuestion.etag, text, ...(option ? { selectedOptionId: option.optionId } : {}) }) });
      }
      await loadProject(project.projectId);
      const remaining = await loadInbox(project.projectId);
      if (selectedQuestion.questionId.startsWith("question.alignment.") && remaining.length === 0) setView("overview");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save the decision."); }
    finally { setDecisionBusy(false); }
  }

  async function switchProject(id: string) {
    try { await loadProject(id); await loadInbox(id); setView("overview"); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the project."); }
  }

  return <div className="amp-shell">
    <aside className="amp-sidebar">
      <div className="amp-brand-row"><button className="panel-toggle" aria-label="Sidebar collapse unavailable" title="Sidebar collapse is not implemented" disabled><span/></button><a className="amp-brand" href="#workspace"><span className="amp-logo"><Layers3 size={16}/></span><b>Northstar</b></a><button className="icon-button" aria-label="Search unavailable" title="Search is not connected" disabled><Search size={15}/></button><button className="new-thread" aria-label="New project" title="New project" onClick={openProjectDialog}><Plus size={16}/></button></div>
      <button className="amp-project-picker" onClick={() => setProjectsOpen((open) => !open)}><Folder size={15}/><span>{project?.name || "Projects"}</span><ChevronDown size={13}/></button>
      {projectsOpen && <div className="project-menu">{projects.map((item)=><button key={item.projectId} className={item.projectId===projectId?"current":""} onClick={()=>{void switchProject(item.projectId);setProjectsOpen(false);}}>{item.name}</button>)}<button onClick={()=>{openProjectDialog();setProjectsOpen(false);}}><Plus size={13}/>New project</button></div>}
      <div className="sidebar-section-head"><span>PROJECT</span></div>
      <nav className="amp-sidebar-links" aria-label="Project navigation"><button onClick={() => setView("overview")} className={view === "overview" ? "current" : ""}><Layers3 size={15}/>Overview</button><button onClick={() => setView("board")} className={view === "board" ? "current" : ""}><FolderKanban size={15}/>Task board <span>{workItems.length || ""}</span></button><button onClick={() => setView("chat")} className={view === "chat" ? "current" : ""}><MessageSquare size={15}/>Chat <span className="nav-status">not connected</span></button><button onClick={() => setView("inbox")} className={view === "inbox" ? "current" : ""}><Inbox size={15}/>Your input <span>{openQuestionCount || ""}</span></button></nav>
      <div className="sidebar-spacer"/>
      <div className="amp-sidebar-links"><button onClick={openProjectDialog}><FolderKanban size={15}/>New project</button></div>
      <div className="amp-user"><span className="amp-avatar">V</span><span><b>Local user</b><small>Prototype · not authenticated</small></span><CircleHelp size={15}/></div>
    </aside>
    <main className="goal-main">
      <header className="goal-topbar"><div className="goal-topbar-title"><span className="project-dot"/><div><h1>{view === "inbox" ? selectedQuestion?.questionId.startsWith("question.alignment.") ? "Alignment interview" : "Your input" : view === "board" ? "Task board" : view === "chat" ? "Project chat" : project?.name || "Project"}</h1><small>{view === "inbox" ? selectedQuestion?.questionId.startsWith("question.alignment.") ? "Repo-grounded alignment · one segment at a time" : "Decisions that need your attention" : view === "board" ? "Work at a glance · click cards for details" : view === "chat" ? "Conversation with the project assistant" : "Goal, outcomes, and what needs attention"}</small></div></div><div className="goal-topbar-actions"><span className="prototype-status"><ShieldAlert size={13}/>Prototype · test data only</span>{(view === "overview" || view === "board") && (openQuestionCount > 0 ? <button className="button secondary" onClick={() => setView("inbox")}><Inbox size={14}/>Review decisions</button> : breakdownItems.length > 0 && view === "overview" ? <button className="button primary" onClick={() => setView("board")}><FolderKanban size={14}/>See task status</button> : <button className="button primary" onClick={() => setWorkDialog(true)}><Plus size={14}/>Add outcome or step</button>)}</div></header>
      <div className="goal-stage">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {view === "inbox" ? <InboxView questions={questions} selected={selectedQuestion} selectedOption={selectedOption} setSelectedOption={setSelectedOption} note={answerNote} setNote={setAnswerNote} busy={decisionBusy} choose={chooseQuestion} answer={() => void submitDecision(false)} reject={() => void submitDecision(true)}/> : view === "board" && project ? <KanbanBoard items={workItems} busy={busy} decide={decideWork}/> : view === "chat" && project ? <ProjectChat projectName={project.name}/> : project ? <div className="goal-scroll">
          <section className="goal-hero"><div className="eyebrow"><Sparkles size={13}/> {alignmentPending ? "PROVISIONAL REPOSITORY HYPOTHESIS" : "PROJECT DIRECTION"}</div><h2>{project.charter.goals.join(" ") || "No project goal defined"}</h2>{alignmentPending && <p className="goal-context-note">This wording comes from the repository, not from a confirmed conversation with you. It is not yet the agreed overarching vision.</p>}<div className="goal-project-meta"><span>{project.name}</span><span>Charter revision {project.charter.revision}</span></div></section>
          <section className={`project-pulse ${openQuestionCount ? "needs-input" : ""}`} aria-labelledby="pulse-title"><div className="pulse-icon"><Sparkles size={17}/></div><div className="pulse-copy"><div className="eyebrow">PROJECT STATE</div><h3 id="pulse-title">{pulse.title}</h3><p>{pulse.description}</p></div>{openQuestionCount > 0 && <button className="button secondary" onClick={() => setView("inbox")}>Your input <span aria-hidden="true">→</span></button>}</section>
          <div className="goal-columns">
            <section className="goal-card outcome-card"><div className="goal-card-heading"><div><div className="eyebrow">PROJECT BREAKDOWN</div><h3>Goal → outcomes → workstreams → steps</h3></div><Layers3 size={17}/></div>{breakdownItems.length ? <WorkTree items={breakdownItems}/> : alignmentPending ? <p className="empty-copy">Alignment is still in progress. The current approved scope will appear here as outcome proposals; nothing starts automatically.</p> : alignmentCompleted ? <p className="empty-copy">The interview answers are recorded. This prototype cannot generate an AI-authored board yet; add proposed outcomes here, then approve each task on the board.</p> : <p className="empty-copy">No outcome hierarchy is recorded yet. Add a meaningful outcome when you are ready.</p>}</section>
            <section className="goal-card attention-card"><div className="goal-card-heading"><div><div className="eyebrow">YOUR INPUT</div><h3>{openQuestionCount ? `${openQuestionCount} ${openQuestionCount === 1 ? "question" : "questions"} to answer` : "No open project questions"}</h3></div><Inbox size={17}/></div>{questions.length ? <div className="attention-list">{questions.slice(0,3).map((question)=><button key={question.questionId} className="attention-item" onClick={()=>void openQuestion(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking ? "Needed before the alignment phase can continue" : "May shape the project direction"}</small></span><span aria-hidden="true">→</span></button>)}</div> : <p className="empty-copy">No decision is waiting on you. {breakdownItems.length ? "The task board shows the proposed work; no agent work is running." : "The project breakdown will appear here when proposals are recorded."}</p>}</section>
          </div>
          <details className="detail-disclosure"><summary><span><b>Process and work-item details</b><small>Proposals, statuses, and implementation detail</small></span><span className="detail-count">{workItems.length}</span></summary>{workItems.length ? <div className="plan-list">{workItems.map((item)=><article className="plan-item" key={item.workId}><div className="plan-item-title"><span className={`thread-status-dot ${item.status}`}/><b>{item.title}</b><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div><p>{item.objective}</p><small>{item.workType} · revision {item.revision}{item.parentWorkId ? ` · parent ${workItems.find((parent)=>parent.workId===item.parentWorkId)?.title || item.parentWorkId}` : ""}</small></article>)}</div> : <p className="empty-copy">No work items have been agreed.</p>}</details>
          {project.charter.acceptanceCriteria.length > 0 && <details className="detail-disclosure"><summary><span><b>Recorded acceptance criteria</b><small>Stored wording only; nothing is automatically verified</small></span><span className="detail-count">{project.charter.acceptanceCriteria.length}</span></summary><ul className="criteria-list">{project.charter.acceptanceCriteria.map((criterion,index)=><li key={`${index}-${criterion}`}>{criterion}</li>)}</ul></details>}
          {summary?.alignment && <details className="detail-disclosure alignment-record"><summary><span><b>Repository alignment record</b><small>{summary.alignment.status === "completed" ? "Interview complete · answers retained as project input" : "Interview in progress · confirmed and open segments"}</small></span><span className="detail-count">{summary.alignment.segments.filter((segment)=>segment.status === "answered").length}/{summary.alignment.segments.length}</span></summary><div className="alignment-record-meta">{summary.alignment.repositoryName} · {summary.alignment.branch} · {summary.alignment.head.slice(0,12)}</div>{summary.alignment.segments.map((segment)=><article className="alignment-record-segment" key={segment.index}><div className="alignment-record-title"><b>{segment.index}. {segment.title}</b><span>{segment.status.replaceAll("_"," ")}</span></div><p>{segment.prompt}</p><details><summary>Repository evidence reviewed</summary><p>{segment.evidence}</p></details><b className="alignment-answer-label">Your recorded response</b><p className="alignment-answer">{segment.answer?.text || "Not answered yet."}</p></article>)}</details>}
          <details className="detail-disclosure history-disclosure"><summary><span><b>Project history</b><small>Event-by-event detail, kept out of the main progress view</small></span><span className="detail-count">{summary?.recentEvents.length || 0}</span></summary>{summary?.recentEvents.length ? <ol className="history-list">{summary.recentEvents.map((event)=><li key={event.eventId}><span><b>{event.title}</b><small>{event.description}</small><time>{event.actorLabel} · {new Date(event.occurredAt).toLocaleString()}</time></span></li>)}</ol> : <p className="empty-copy">No project events have been recorded yet.</p>}</details>
          <div className="goal-footnote"><ShieldAlert size={13}/>No project-scoped model or worker is connected. This prototype does not launch installed CLI tools or report agent progress or verified outcomes.</div>
        </div> : <div className="loading">Loading your project…</div>}
      </div>
    </main>
    {projectDialog && <Modal title="Add a project" onClose={() => setProjectDialog(false)}>
      {projectMode === "choose" ? <div className="project-type-choice"><p>How should we start? Existing projects begin with a read-only repository review and a guided alignment interview.</p><button className="project-type-card" onClick={()=>{setProjectMode("new");setDraftName("");setDraftGoal("");}}><b>New project</b><small>Start from a goal you describe.</small></button><button className="project-type-card" onClick={()=>{setProjectMode("existing");setDraftName("");setDraftGoal("");}}><b>Existing project</b><small>Inspect a local repository, review what it says, then answer a five-part alignment interview.</small></button><button type="button" className="button secondary" onClick={()=>setProjectDialog(false)}>Cancel</button></div> : <>
        <button type="button" className="back-link" onClick={()=>setProjectMode("choose")}>← Choose project type</button>
        {projectMode === "existing" && <section className="repo-inspector"><label>Local Git repository root<input value={repositoryPath} onChange={(event)=>{setRepositoryPath(event.target.value);setRepoReport(null);}} autoFocus placeholder="/home/you/code/project"/></label><div className="form-actions"><button type="button" className="button secondary" disabled={inspectionBusy} onClick={()=>void inspectRepository()}>{inspectionBusy ? "Inspecting…" : "Inspect repository"}</button></div>{repoReport && <RepoInspectionPreview report={repoReport}/>}<p className="form-hint">Read-only local scan. Only tracked Markdown/RST project docs are opened; ignored/untracked files, code, data, credentials, and generated folders are excluded. Nothing is sent to a model.</p></section>}
        <form onSubmit={createProject}>
          {(projectMode === "new" || repoReport) && <><label>Project name<input value={draftName} onChange={(event)=>setDraftName(event.target.value)} required maxLength={100} autoFocus={projectMode === "new"} placeholder="e.g. Reading list"/></label><label>Initial goal or vision<textarea value={draftGoal} onChange={(event)=>setDraftGoal(event.target.value)} required maxLength={1000} rows={3} placeholder="What do you want to be true when this succeeds?"/></label>{projectMode === "existing" && <p className="form-hint">This is only the starting hypothesis. After creation, the project will ask about its vision, current state, ownership/scope, unresolved choices, and success/approval preferences.</p>}<div className="form-actions"><button type="button" className="button secondary" onClick={() => setProjectDialog(false)}>Cancel</button><button className="button primary" disabled={busy || (projectMode === "existing" && !repoReport)}>{busy ? "Creating…" : projectMode === "existing" ? "Create project & start alignment" : "Create project"}</button></div></>}
        </form>
      </>}
    </Modal>}
    {workDialog && <Modal title="Add an outcome or nested step" onClose={() => setWorkDialog(false)}><form onSubmit={createWork}><label>Meaningful result<input name="title" required maxLength={160} autoFocus placeholder="Describe the outcome or workstream"/></label><label>Why does it advance the project goal?<textarea name="objective" required maxLength={2000} rows={3} placeholder="Describe the result, not an agent's internal checklist."/></label><label>Parent outcome or workstream<select name="parentWorkId"><option value="">Top-level project outcome</option>{workItems.filter((item)=>item.workId!=="work.alignment-intake"&&item.status!=="cancelled").map((item)=><option key={item.workId} value={item.workId}>{item.title} · {item.status.replaceAll("_"," ")}</option>)}</select></label><label>Category<select name="workType"><option value="research">Research</option><option value="analysis">Analysis</option><option value="planning">Planning</option><option value="implementation">Implementation</option><option value="review">Review</option><option value="writing">Writing</option><option value="operations">Operations</option></select></label><label>Acceptance criteria (optional, one per line)<textarea name="acceptanceCriteria" maxLength={4000} rows={2} placeholder="Only add criteria you actually want to judge the result by."/></label><p className="form-hint">This records a proposal in the project hierarchy; it does not start an agent or claim progress.</p><div className="form-actions"><button type="button" className="button secondary" onClick={() => setWorkDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Saving…" : "Add proposal"}</button></div></form></Modal>}
  </div>;
}

function projectPulse(workCount:number,questionCount:number,blockingCount:number,alignmentQuestionCount:number,alignmentWork?:Work,breakdownCount=0,runningCount=0,alignmentCompleted=false) {
  if (blockingCount > 0) return { title: "Your input is needed", description: `${blockingCount} open question${blockingCount === 1 ? " is" : "s are"} blocking affected work. Review the impact before deciding.` };
  if (alignmentQuestionCount > 0) return { title: "Your project direction needs alignment", description: "Some project scope is still unresolved. The current proposal is visible on the task board; unresolved choices are not being guessed." };
  if (runningCount > 0) return { title: `${runningCount} work item${runningCount === 1 ? " is" : "s are"} in progress`, description: "This count comes from canonical work statuses. Open the task board for the exact items; no unrecorded progress is inferred." };
  if (breakdownCount > 0) return { title: "Nothing is running right now", description: `${breakdownCount} work items are visible on the task board; proposed and ready statuses are not active execution. This prototype has no connected project agent.` };
  if (alignmentCompleted) return { title: "Alignment interview complete", description: "Your repo-grounded answers are stored in project history. The prototype has no project AI to draft a delivery board yet; add outcome proposals manually, and approve each item before it becomes Ready." };
  if (alignmentWork && !["completed", "cancelled"].includes(alignmentWork.status)) return { title: "Planning is underway", description: "No task breakdown is recorded yet. The project will show proposed outcomes here before claiming any work has started." };
  if (questionCount > 0) return { title: "A decision may shape the project", description: "There are open project questions to review; no progress is inferred from the plan." };
  if (workCount > 0) return { title: "Work is recorded · nothing running", description: "The task board shows canonical item statuses. This prototype has no connected agent and does not infer progress from a proposal." };
  return { title: "Project direction is recorded", description: "No outcome hierarchy is recorded yet. Add a meaningful outcome when you are ready; this prototype will not invent a finish line." };
}

function WorkTree({items,showObjectives=false}:{items:Work[];showObjectives?:boolean}) {
  const visible = items.filter((item)=>item.status !== "cancelled");
  const byId = new Map(visible.map((item)=>[item.workId,item]));
  const children = new Map<string,Work[]>();
  for (const item of visible) {
    if (item.parentWorkId && byId.has(item.parentWorkId)) {
      const siblings = children.get(item.parentWorkId) || [];
      siblings.push(item); children.set(item.parentWorkId,siblings);
    }
  }
  const roots = visible.filter((item)=>!item.parentWorkId || !byId.has(item.parentWorkId));
  if (!roots.length) return <p className="empty-copy">No agreed outcomes are recorded. Work should be added beneath a confirmed project goal, not invented from repository checklists.</p>;
  const render = (item:Work,depth:number):React.ReactNode => <li key={item.workId}>
    <article className={`outcome-node depth-${Math.min(depth,3)}`}>
      <div className="outcome-node-heading"><span className="outcome-level">{depth===0?"OUTCOME":depth===1?"WORKSTREAM":"STEP"}</span><b>{item.title}</b><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div>
      {(showObjectives || depth===0) && <p>{item.objective}</p>}
      {showObjectives && item.acceptanceCriteria?.length>0 && <small>{item.acceptanceCriteria.length} stated acceptance criterion/criteria · not automatically verified</small>}
    </article>
    {(children.get(item.workId)||[]).length>0 && <ol>{(children.get(item.workId)||[]).map((child)=>render(child,depth+1))}</ol>}
  </li>;
  return <ol className={`outcome-tree ${showObjectives?"detailed":""}`}>{roots.map((root)=>render(root,0))}</ol>;
}

function KanbanBoard({items,busy,decide}:{items:Work[];busy:boolean;decide:(workId:string,revision:number,decision:"approve"|"reject")=>Promise<boolean>}) {
  const [selectedWork,setSelectedWork]=React.useState<Work|null>(null);
  const columns = [
    { id: "proposed", title: "Proposed", statuses: ["proposed"] },
    { id: "ready", title: "Ready", statuses: ["ready"] },
    { id: "active", title: "In progress", statuses: ["running", "verifying"] },
    { id: "waiting", title: "Waiting", statuses: ["waiting_human", "blocked"] },
    { id: "done", title: "Done", statuses: ["completed"] },
  ];
  const byId = new Map(items.map((item)=>[item.workId,item]));
  const stopped = items.filter((item)=>["failed","cancelled"].includes(item.status));
  return <div className="board-page">
    <header className="board-toolbar"><div><div className="eyebrow">PROJECT BOARD</div><h2>Work items</h2><p>Click a card to see its details and approval controls.</p></div><div className="board-toolbar-actions"><span className="board-item-total">{items.length} items</span><span className="auto-run-status"><span>Auto-run</span><button role="switch" aria-checked="false" disabled title="Unavailable until provider/data policy, spend limits, and worker isolation are configured."><i/>Off</button></span></div></header>
    <section className="kanban-board" aria-label="Project work kanban board">{columns.map((column)=>{
      const cards=items.filter((item)=>column.statuses.includes(item.status));
      return <div className="kanban-column" key={column.id}><header><h3>{column.title}</h3><span>{cards.length}</span></header>{cards.length ? <div className="kanban-cards">{cards.map((item)=>{
        const parent=item.parentWorkId?byId.get(item.parentWorkId):undefined;
        return <button className="kanban-card" key={item.workId} onClick={()=>setSelectedWork(item)} aria-label={`Open ${item.title} details`}><div className="kanban-card-meta"><span className={`thread-status-dot ${item.status}`}/><span>{item.workType}</span><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div><h4>{item.title}</h4>{parent&&<small>Under: {parent.title}</small>}</button>;
      })}</div> : <p className="kanban-empty">Empty</p>}</div>;
    })}</section>
    {stopped.length>0&&<details className="stopped-items"><summary>Stopped · {stopped.length}</summary><div>{stopped.map((item)=><button key={item.workId} onClick={()=>setSelectedWork(item)}>{item.title}<span>{item.status}</span></button>)}</div></details>}
    {selectedWork&&<Modal title={selectedWork.title} onClose={()=>setSelectedWork(null)}><div className="work-detail-modal"><div className="work-detail-meta"><span className={`work-status ${selectedWork.status}`}><i/>{selectedWork.status.replaceAll("_"," ")}</span><span>{selectedWork.workType}</span>{selectedWork.parentWorkId&&<span>Under {byId.get(selectedWork.parentWorkId)?.title||selectedWork.parentWorkId}</span>}</div><p>{selectedWork.objective}</p>{selectedWork.acceptanceCriteria.length>0&&<div><b>Acceptance criteria</b><ul>{selectedWork.acceptanceCriteria.map((criterion,index)=><li key={`${index}-${criterion}`}>{criterion}</li>)}</ul></div>}{selectedWork.status==="proposed"?<><p className="form-hint">Approval moves this item to Ready; it does not start execution.</p><div className="form-actions"><button className="button secondary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"reject"))setSelectedWork(null);}}>Decline</button><button className="button primary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"approve"))setSelectedWork(null);}}>Approve task</button></div></>:selectedWork.status==="ready"?<p className="form-hint">Approved and Ready. No agent is connected to execute it.</p>:null}</div></Modal>}
  </div>;
}

function ProjectChat({projectName}:{projectName:string}) {
  return <div className="chat-page"><section className="chat-heading"><div className="eyebrow">PROJECT CONVERSATION</div><h2>Talk through {projectName}</h2><p>Chat is meant to help discuss the goal and the work—not replace the outcome hierarchy or task board.</p></section><section className="chat-unavailable"><div className="chat-agent-mark"><MessageSquare size={18}/></div><div><div className="eyebrow">AI CONNECTION STATUS</div><h3>Project chat is not connected yet</h3><p>This screen currently has no project-scoped AI service. Messages typed here would not reach an AI, so the composer is intentionally disabled rather than pretending to send them. You can still talk with me in this coding conversation.</p><p>Connecting chat requires a project-specific provider/data policy and enforceable usage controls; it must also read the same canonical goal and work tree shown here.</p></div></section><div className="chat-composer disabled"><textarea aria-label="Chat message" disabled placeholder="AI chat is unavailable in this prototype"/><button className="button primary" disabled>Send</button><small>No text is saved or sent from this disabled composer.</small></div></div>;
}

function InboxView({questions,selected,selectedOption,setSelectedOption,note,setNote,busy,choose,answer,reject}:{questions:Question[];selected:QuestionDetail|null;selectedOption:string;setSelectedOption:(value:string)=>void;note:string;setNote:(value:string)=>void;busy:boolean;choose:(id:string)=>void;answer:()=>void;reject:()=>void}) {
  return <div className="inbox-layout"><section className="inbox-questions"><div className="inbox-heading"><span>OPEN DECISIONS</span><span>{questions.length}</span></div>{questions.length ? questions.map((question)=><button key={question.questionId} className={`question-link ${selected?.questionId===question.questionId?"chosen":""}`} onClick={()=>choose(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking?"Your answer is needed to continue":"May affect project direction"}</small></span></button>) : <div className="inbox-empty"><Inbox size={19}/><b>Nothing needs your attention</b><small>No open project decisions.</small></div>}</section><section className="inbox-detail">{selected ? <><div className="eyebrow">{selected.blocking?"YOUR INPUT IS NEEDED":"PROJECT DECISION"}</div><h2>{selected.questionText}</h2>{selected.questionId.startsWith("question.alignment.")&&<div className="eyebrow">WHAT I SAW · REPOSITORY EVIDENCE</div>}<p className={selected.questionId.startsWith("question.alignment.") ? "alignment-evidence" : ""}>{selected.record.impact}</p>{selected.affectedWork.length>0&&<details className="affected-details"><summary>Why this matters <span>{selected.affectedWork.length} affected planned item{selected.affectedWork.length===1?"":"s"}</span></summary><div>{selected.affectedWork.map((item)=><span key={item.workId}>{item.title}</span>)}</div></details>}{selected.record.options.length>0&&<fieldset className="decision-options"><legend>Choose an option</legend>{selected.record.options.map((option)=><label key={option.optionId}><input type="radio" name="decision" value={option.optionId} checked={selectedOption===option.optionId} onChange={()=>setSelectedOption(option.optionId)}/><span><b>{option.label}</b>{option.consequence&&<small>{option.consequence}</small>}</span></label>)}</fieldset>}<label className="note-label">{selected.record.options.length?"ADDITIONAL CONTEXT (OPTIONAL)":"YOUR RESPONSE"}<textarea value={note} onChange={(event)=>setNote(event.target.value)} rows={3} placeholder={selected.record.options.length ? "Add context for this decision…" : "Write your response; partial answers are fine."}/></label><div className="decision-actions">{!selected.questionId.startsWith("question.alignment.") && <button className="button secondary" disabled={busy} onClick={reject}>Reject question</button>}<button className="button primary" disabled={busy} onClick={answer}>{busy?"Saving…":selected.record.questionType==="clarification"?"Record response":"Record decision"}</button></div><p className="form-hint">{selected.record.questionType==="clarification"?"Your response is saved as project input; it does not approve execution or start an agent.":"Your decision is checked against the current project state."}</p></> : <div className="inbox-empty"><Inbox size={19}/><b>Select a decision</b><small>Review its impact and record your direction.</small></div>}</section></div>;
}

function RepoInspectionPreview({report}:{report:RepoReport}) {
  return <div className="repo-inspection-preview"><div className="repo-facts"><b>{report.repositoryName}</b><span>{report.branch} · {report.head.slice(0,12)}</span><span>{report.trackedFileCount} eligible tracked paths · working-tree status not inspected</span></div><div className="eyebrow">WHAT I FOUND IN TRACKED DOCS</div>{report.topLevelAreas.length>0&&<p><b>Top-level areas:</b> {report.topLevelAreas.join(", ")}</p>}{report.documents.map((doc)=><details key={doc.path} className="repo-doc"><summary>{doc.path} · {doc.title}</summary><p>{doc.excerpt}</p></details>)}{!report.documents.length&&<p>No eligible README/context/docs were found; the interview will treat that as unknown rather than inventing a project goal.</p>}<p className="inspection-policy">{report.inspectionPolicy}</p></div>;
}

function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:React.ReactNode}) { return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17}/></button></div>{children}</section></div>; }

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
