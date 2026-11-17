import React from "react";
import { createRoot } from "react-dom/client";
import {
  ChevronDown, CircleHelp, Folder, FolderKanban, Inbox,
  Activity as ActivityIcon, Layers3, MessageSquare, Plus, Search, ShieldAlert, Sparkles, X,
} from "lucide-react";
import "./style.css";
import "./goal.css";
import "./work-views.css";
import "./kanban-board.css";
import "./chat.css";

type Project = { projectId: string; name: string; status: string; charter: { revision: number; goals: string[]; nonGoals: string[]; acceptanceCriteria: string[] } };
type Work = { workId: string; title: string; objective: string; workType: string; status: string; parentWorkId?: string | null; dependsOn: string[]; acceptanceCriteria: string[]; revision: number };
type ProjectEvent = { eventId: string; title: string; description: string; actorLabel: string; occurredAt: string };
type AlignmentSegment = { index: number; title: string; prompt: string; evidence: string; status: string; answer: { text: string } | null };
type Alignment = { status: string; repositoryName: string | null; branch: string | null; head: string | null; segments: AlignmentSegment[] };
type Activity = { id: string; workId: string; workTitle: string; actor: string; text: string; at: string };
type Automation = { autoRun: boolean; workerAvailable: boolean; demoProject: boolean };
type Log = { id: string; kind: string; title: string; detail: string; crucial: boolean; edited?: boolean };
type Memory = { memory: Record<string, string[]>; detailedLogs?: Log[]; notYetMergedNotes: string[] };
type Summary = { dataRoots?: string[]; memory?: Memory; automation: Automation; activity: Activity[]; drafting: boolean; project: Project; workItems: Work[]; recentEvents: ProjectEvent[]; counts: Record<string, number>; alignment?: Alignment | null };
type RepoReport = { repositoryName: string; repositoryPath: string; head: string; branch: string; trackedFileCount: number; trackedChanges: number | null; topLevelAreas: string[]; documents: { path: string; title: string; excerpt: string }[]; goalHint: string; fingerprint: string; inspectionPolicy: string };
type ProjectMode = "choose" | "new" | "existing";
type Question = { questionId: string; questionType: string; questionText: string; priority: string; blocking: boolean; workIds: string[]; etag: string };
type QuestionDetail = Question & { record: { questionType: string; options: { optionId: string; label: string; consequence?: string }[]; impact: string; status: string; questionText: string }; affectedWork: { workId: string; title: string; status: string }[] };
type Turn = { role: "user" | "assistant"; content: string };
type View = "overview" | "board" | "activity" | "chat" | "inbox";

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

  React.useEffect(() => {
    const awaitingBoard = summary?.alignment?.status === "completed" && !summary.automation.demoProject && !summary.workItems.some((w) => w.workId !== "work.alignment-intake");
    const live = summary && (summary.drafting || awaitingBoard || summary.automation.autoRun || summary.workItems.some((w) => ["running", "verifying", "ready"].includes(w.status)));
    if (!live) return;
    const timer = window.setInterval(() => { loadProject(projectId).catch(() => {}); loadInbox(projectId).catch(() => {}); }, 4000);
    return () => window.clearInterval(timer);
  }, [summary, projectId]);

  async function setAutoRun(enabled: boolean) {
    setBusy(true);
    try { await request(`/api/v1/projects/${encodeURIComponent(projectId)}/automation`, { method: "POST", body: JSON.stringify({ autoRun: enabled }) }); await loadProject(projectId); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not change auto-run."); }
    finally { setBusy(false); }
  }

  async function draftBoard() {
    setBusy(true);
    try { await request(`/api/v1/projects/${encodeURIComponent(projectId)}/board/draft`, { method: "POST", body: JSON.stringify({}) }); await loadProject(projectId); setView("board"); }
    catch (e) { setError(e instanceof Error ? e.message : "The VP could not draft a board."); }
    finally { setBusy(false); }
  }

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
      setView("inbox"); setProjectDialog(false);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create project."); }
    finally { setBusy(false); }
  }

  async function decideWork(workId: string, revision: number, decision: "approve" | "reject" | "retry"): Promise<boolean> {
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
      setSelectedQuestion(detail);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load question."); }
  }

  async function openQuestion(questionId: string) {
    await chooseQuestion(questionId);
    setView("inbox");
  }

  async function questionRecorded() {
    if (!project || !selectedQuestion) return;
    const wasAlignment = selectedQuestion.questionId.startsWith("question.alignment.");
    await loadProject(project.projectId);
    const remaining = await loadInbox(project.projectId);
    if (wasAlignment && remaining.length === 0) setView("overview");
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
      <nav className="amp-sidebar-links" aria-label="Project navigation"><button onClick={() => setView("overview")} className={view === "overview" ? "current" : ""}><Layers3 size={15}/>Overview</button><button onClick={() => setView("board")} className={view === "board" ? "current" : ""}><FolderKanban size={15}/>Task board <span>{workItems.length || ""}</span></button><button onClick={() => setView("activity")} className={view === "activity" ? "current" : ""}><ActivityIcon size={15}/>Activity <span>{runningWorkCount ? "live" : ""}</span></button><button onClick={() => setView("chat")} className={view === "chat" ? "current" : ""}><MessageSquare size={15}/>Chat with VP</button><button onClick={() => setView("inbox")} className={view === "inbox" ? "current" : ""}><Inbox size={15}/>Your input <span>{openQuestionCount || ""}</span></button></nav>
      <div className="sidebar-spacer"/>
      <div className="amp-sidebar-links"><button onClick={openProjectDialog}><FolderKanban size={15}/>New project</button></div>
      <div className="amp-user"><span className="amp-avatar">V</span><span><b>Local user</b><small>Prototype · not authenticated</small></span><CircleHelp size={15}/></div>
    </aside>
    <main className="goal-main">
      <header className="goal-topbar"><div className="goal-topbar-title"><span className="project-dot"/><div><h1>{view === "inbox" ? selectedQuestion?.questionId.startsWith("question.alignment.") ? "Alignment interview" : "Your input" : view === "board" ? "Task board" : view === "activity" ? "Activity" : view === "chat" ? "Project chat" : project?.name || "Project"}</h1><small>{view === "inbox" ? selectedQuestion?.questionId.startsWith("question.alignment.") ? "Repo-grounded alignment · one segment at a time" : "Decisions that need your attention" : view === "board" ? "Work at a glance · click cards for details" : view === "activity" ? "What your agents are doing and have done" : view === "chat" ? "Conversation with the project assistant" : "Goal, outcomes, and what needs attention"}</small></div></div><div className="goal-topbar-actions"><span className="prototype-status"><ShieldAlert size={13}/>Prototype · test data only</span>{(view === "overview" || view === "board") && (openQuestionCount > 0 ? <button className="button secondary" onClick={() => setView("inbox")}><Inbox size={14}/>Review decisions</button> : breakdownItems.length > 0 && view === "overview" ? <button className="button primary" onClick={() => setView("board")}><FolderKanban size={14}/>See task status</button> : <button className="button primary" onClick={() => setWorkDialog(true)}><Plus size={14}/>Add outcome or step</button>)}</div></header>
      <div className="goal-stage">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {view === "inbox" ? <InboxView projectId={project?.projectId || ""} questions={questions} selected={selectedQuestion} choose={chooseQuestion} recorded={questionRecorded}/> : view === "board" && project ? <KanbanBoard items={workItems} busy={busy} decide={decideWork} automation={summary?.automation} setAutoRun={setAutoRun} draftBoard={draftBoard} drafting={Boolean(summary?.drafting)} alignmentCompleted={alignmentCompleted}/> : view === "activity" && project ? <ActivityView items={summary?.activity || []} running={runningWorkCount} workItems={workItems}/> : view === "chat" && project ? <ProjectChat projectId={project.projectId} projectName={project.name}/> : project ? <div className="goal-scroll">
          <section className="goal-hero"><div className="eyebrow"><Sparkles size={13}/> {alignmentPending ? "PROVISIONAL REPOSITORY HYPOTHESIS" : "PROJECT DIRECTION"}</div><h2>{project.charter.goals.join(" ") || "No project goal defined"}</h2>{alignmentPending && <p className="goal-context-note">This wording comes from the repository, not from a confirmed conversation with you. It is not yet the agreed overarching vision.</p>}<div className="goal-project-meta"><span>{project.name}</span><span>Charter revision {project.charter.revision}</span></div></section>
          <section className={`project-pulse ${openQuestionCount ? "needs-input" : ""}`} aria-labelledby="pulse-title"><div className="pulse-icon"><Sparkles size={17}/></div><div className="pulse-copy"><div className="eyebrow">PROJECT STATE</div><h3 id="pulse-title">{pulse.title}</h3><p>{pulse.description}</p></div>{openQuestionCount > 0 && <button className="button secondary" onClick={() => setView("inbox")}>Your input <span aria-hidden="true">→</span></button>}</section>
          {summary&&!summary.automation.demoProject&&<DataRootsCard projectId={project.projectId} roots={summary.dataRoots||[]} reload={()=>loadProject(project.projectId)}/>}
          {summary?.memory&&<MemoryCard memory={summary.memory} onCorrect={()=>setView("chat")}/>}
          <div className="goal-columns">
            <section className="goal-card outcome-card"><div className="goal-card-heading"><div><div className="eyebrow">PROJECT BREAKDOWN</div><h3>Goal → outcomes → workstreams → steps</h3></div><Layers3 size={17}/></div>{breakdownItems.length ? <WorkTree items={breakdownItems}/> : alignmentPending ? <p className="empty-copy">Alignment is still in progress. The current approved scope will appear here as outcome proposals; nothing starts automatically.</p> : alignmentCompleted ? <p className="empty-copy">The interview answers are recorded. The VP is drafting your task board; if nothing appears, use “Draft board with VP” on the Task board.</p> : <p className="empty-copy">No outcome hierarchy is recorded yet. Add a meaningful outcome when you are ready.</p>}</section>
            <section className="goal-card attention-card"><div className="goal-card-heading"><div><div className="eyebrow">YOUR INPUT</div><h3>{openQuestionCount ? `${openQuestionCount} ${openQuestionCount === 1 ? "question" : "questions"} to answer` : "No open project questions"}</h3></div><Inbox size={17}/></div>{questions.length ? <div className="attention-list">{questions.slice(0,3).map((question)=><button key={question.questionId} className="attention-item" onClick={()=>void openQuestion(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking ? "Needed before the alignment phase can continue" : "May shape the project direction"}</small></span><span aria-hidden="true">→</span></button>)}</div> : <p className="empty-copy">No decision is waiting on you. {breakdownItems.length ? "The task board shows the proposed work; no agent work is running." : "The project breakdown will appear here when proposals are recorded."}</p>}</section>
          </div>
          <details className="detail-disclosure"><summary><span><b>Process and work-item details</b><small>Proposals, statuses, and implementation detail</small></span><span className="detail-count">{workItems.length}</span></summary>{workItems.length ? <div className="plan-list">{workItems.map((item)=><article className="plan-item" key={item.workId}><div className="plan-item-title"><span className={`thread-status-dot ${item.status}`}/><b>{item.title}</b><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div><p>{item.objective}</p><small>{item.workType} · revision {item.revision}{item.parentWorkId ? ` · parent ${workItems.find((parent)=>parent.workId===item.parentWorkId)?.title || item.parentWorkId}` : ""}</small></article>)}</div> : <p className="empty-copy">No work items have been agreed.</p>}</details>
          {project.charter.acceptanceCriteria.length > 0 && <details className="detail-disclosure"><summary><span><b>Recorded acceptance criteria</b><small>Stored wording only; nothing is automatically verified</small></span><span className="detail-count">{project.charter.acceptanceCriteria.length}</span></summary><ul className="criteria-list">{project.charter.acceptanceCriteria.map((criterion,index)=><li key={`${index}-${criterion}`}>{criterion}</li>)}</ul></details>}
          {summary?.alignment && <details className="detail-disclosure alignment-record"><summary><span><b>Alignment record</b><small>{summary.alignment.status === "completed" ? "Interview complete · answers retained as project input" : "Interview in progress · confirmed and open segments"}</small></span><span className="detail-count">{summary.alignment.segments.filter((segment)=>segment.status === "answered").length}/{summary.alignment.segments.length}</span></summary>{summary.alignment.head&&<div className="alignment-record-meta">{summary.alignment.repositoryName} · {summary.alignment.branch} · {summary.alignment.head.slice(0,12)}</div>}{summary.alignment.segments.map((segment)=><article className="alignment-record-segment" key={segment.index}><div className="alignment-record-title"><b>{segment.index}. {segment.title}</b><span>{segment.status.replaceAll("_"," ")}</span></div><p>{segment.prompt}</p><details><summary>Repository evidence reviewed</summary><p>{segment.evidence}</p></details><b className="alignment-answer-label">Your recorded response</b><p className="alignment-answer">{segment.answer?.text || "Not answered yet."}</p></article>)}</details>}
          <details className="detail-disclosure history-disclosure"><summary><span><b>Project history</b><small>Event-by-event detail, kept out of the main progress view</small></span><span className="detail-count">{summary?.recentEvents.length || 0}</span></summary>{summary?.recentEvents.length ? <ol className="history-list">{summary.recentEvents.map((event)=><li key={event.eventId}><span><b>{event.title}</b><small>{event.description}</small><time>{event.actorLabel} · {new Date(event.occurredAt).toLocaleString()}</time></span></li>)}</ol> : <p className="empty-copy">No project events have been recorded yet.</p>}</details>
          <div className="goal-footnote"><ShieldAlert size={13}/>Approved tasks run through local Claude Code or Codex workers in the project workspace. Chat only discusses; it cannot start or change work.</div>
        </div> : <div className="loading">Loading your project…</div>}
      </div>
    </main>
    {projectDialog && <Modal title="Add a project" onClose={() => setProjectDialog(false)}>
      {projectMode === "choose" ? <div className="project-type-choice"><p>How should we start? Every project starts with an alignment interview; existing ones begin with a read-only repository review.</p><button className="project-type-card" onClick={()=>{setProjectMode("new");setDraftName("");setDraftGoal("");}}><b>New project</b><small>Start from a goal you describe, then the VP interviews you.</small></button><button className="project-type-card" onClick={()=>{setProjectMode("existing");setDraftName("");setDraftGoal("");}}><b>Existing project</b><small>Inspect a local repository, review what it says, then answer a five-part alignment interview.</small></button><button type="button" className="button secondary" onClick={()=>setProjectDialog(false)}>Cancel</button></div> : <>
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
  if (breakdownCount > 0) return { title: "Nothing is running right now", description: `${breakdownCount} work items are visible on the task board; proposed and ready statuses are not active execution. Approve a task, or turn on auto-run, to start work.` };
  if (alignmentCompleted) return { title: "Alignment interview complete", description: "Your repo-grounded answers are stored in project history. The VP drafts the task board from them; approve each task, or turn on auto-run." };
  if (alignmentWork && !["completed", "cancelled"].includes(alignmentWork.status)) return { title: "Planning is underway", description: "No task breakdown is recorded yet. The project will show proposed outcomes here before claiming any work has started." };
  if (questionCount > 0) return { title: "A decision may shape the project", description: "There are open project questions to review; no progress is inferred from the plan." };
  if (workCount > 0) return { title: "Work is recorded · nothing running", description: "Approve a task on the board, or turn on auto-run, to start work." };
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

function KanbanBoard({items,busy,decide,automation,setAutoRun,draftBoard,drafting,alignmentCompleted}:{items:Work[];busy:boolean;decide:(workId:string,revision:number,decision:"approve"|"reject"|"retry")=>Promise<boolean>;automation?:Automation;setAutoRun:(enabled:boolean)=>void;draftBoard:()=>void;drafting:boolean;alignmentCompleted:boolean}) {
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
    <header className="board-toolbar"><div><div className="eyebrow">PROJECT BOARD</div><h2>Work items</h2><p>Click a card to see its details and approval controls.</p></div><div className="board-toolbar-actions"><span className="board-item-total">{items.length} items</span>{alignmentCompleted&&items.filter((i)=>i.workId!=="work.alignment-intake").length===0&&!automation?.demoProject&&<button className="button primary" disabled={busy||drafting} onClick={draftBoard}>{drafting?"VP is drafting…":"Draft board with VP"}</button>}<span className="auto-run-status"><span>Auto-run</span><button role="switch" aria-checked={Boolean(automation?.autoRun)} disabled={busy||!automation||automation.demoProject||!automation.workerAvailable} title={automation?.demoProject?"The synthetic demo project cannot run agents.":!automation?.workerAvailable?"No worker CLI (Claude Code or Codex) is available.":"When on, the VP approves and runs every proposed task in dependency order without asking."} onClick={()=>setAutoRun(!automation?.autoRun)}><i/>{automation?.autoRun?"On":"Off"}</button></span></div></header>
    <section className="kanban-board" aria-label="Project work kanban board">{columns.map((column)=>{
      const cards=items.filter((item)=>column.statuses.includes(item.status));
      return <div className="kanban-column" key={column.id}><header><h3>{column.title}</h3><span>{cards.length}</span></header>{cards.length ? <div className="kanban-cards">{cards.map((item)=>{
        const parent=item.parentWorkId?byId.get(item.parentWorkId):undefined;
        return <button className="kanban-card" key={item.workId} onClick={()=>setSelectedWork(item)} aria-label={`Open ${item.title} details`}><div className="kanban-card-meta"><span className={`thread-status-dot ${item.status}`}/><span>{item.workType}</span><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div><h4>{item.title}</h4>{parent&&<small>Under: {parent.title}</small>}{item.status==="proposed"&&<span role="button" tabIndex={0} className="button primary kanban-approve" onClick={(event)=>{event.stopPropagation();void decide(item.workId,item.revision,"approve");}} onKeyDown={(event)=>{if(event.key==="Enter"){event.stopPropagation();void decide(item.workId,item.revision,"approve");}}}>Approve</span>}</button>;
      })}</div> : <p className="kanban-empty">Empty</p>}</div>;
    })}</section>
    {stopped.length>0&&<details className="stopped-items"><summary>Stopped · {stopped.length}</summary><div>{stopped.map((item)=><button key={item.workId} onClick={()=>setSelectedWork(item)}>{item.title}<span>{item.status}</span></button>)}</div></details>}
    {selectedWork&&<Modal title={selectedWork.title} onClose={()=>setSelectedWork(null)}><div className="work-detail-modal"><div className="work-detail-meta"><span className={`work-status ${selectedWork.status}`}><i/>{selectedWork.status.replaceAll("_"," ")}</span><span>{selectedWork.workType}</span>{selectedWork.parentWorkId&&<span>Under {byId.get(selectedWork.parentWorkId)?.title||selectedWork.parentWorkId}</span>}</div><p>{selectedWork.objective}</p>{selectedWork.acceptanceCriteria.length>0&&<div><b>Acceptance criteria</b><ul>{selectedWork.acceptanceCriteria.map((criterion,index)=><li key={`${index}-${criterion}`}>{criterion}</li>)}</ul></div>}{selectedWork.status==="proposed"?<><p className="form-hint">Approving makes this task ready; the VP's agents pick it up in dependency order, one at a time.</p><div className="form-actions"><button className="button secondary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"reject"))setSelectedWork(null);}}>Decline</button><button className="button primary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"approve"))setSelectedWork(null);}}>Approve task</button></div></>:selectedWork.status==="ready"?<p className="form-hint">Approved. It starts as soon as earlier tasks it depends on are done.</p>:selectedWork.status==="failed"?<><p className="form-hint">This task failed or was interrupted. See the Activity tab for the report.</p><div className="form-actions"><button className="button primary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"retry"))setSelectedWork(null);}}>Retry task</button></div></>:null}</div></Modal>}
  </div>;
}

function ActivityView({items,running,workItems}:{items:Activity[];running:number;workItems:Work[]}) {
  const active = workItems.filter((w)=>["running","verifying"].includes(w.status));
  return <div className="goal-scroll"><section className="goal-card"><div className="goal-card-heading"><div><div className="eyebrow">RIGHT NOW</div><h3>{running ? `${running} task${running===1?"":"s"} in progress` : "Nothing running"}</h3></div><ActivityIcon size={17}/></div>{active.length?<ul className="criteria-list">{active.map((w)=><li key={w.workId}><b>{w.title}</b> · {w.workType}</li>)}</ul>:<p className="empty-copy">Approve a task on the board, or turn on auto-run, and agent work shows up here as it happens.</p>}</section>
    <section className="goal-card"><div className="goal-card-heading"><div><div className="eyebrow">REPORTS</div><h3>What agents have reported</h3></div></div>{items.length?<ol className="history-list">{items.map((a)=><li key={a.id}><span><b>{a.workTitle}</b><small style={{whiteSpace:"pre-wrap"}}>{a.text}</small><time>{a.actor} · {new Date(a.at).toLocaleString()}</time></span></li>)}</ol>:<p className="empty-copy">No agent reports yet.</p>}</section></div>;
}

function DataRootsCard({projectId,roots,reload}:{projectId:string;roots:string[];reload:()=>Promise<void>}) {
  const [adding,setAdding]=React.useState("");
  const [error,setError]=React.useState("");
  const [busy,setBusy]=React.useState(false);
  async function save(paths:string[]){setBusy(true);setError("");try{await request(`/api/v1/projects/${encodeURIComponent(projectId)}/data-roots`,{method:"POST",body:JSON.stringify({paths})});setAdding("");await reload();}catch(e){setError(e instanceof Error?e.message:"Could not save the folders.");}finally{setBusy(false);}}
  return <section className="goal-card"><div className="goal-card-heading"><div><div className="eyebrow">LOCAL DATA AGENTS CAN READ</div><h3>{roots.length?`${roots.length} folder${roots.length===1?"":"s"}, read-only`:"No extra data folders"}</h3></div></div>
    {roots.length>0?<ul className="criteria-list">{roots.map((root)=><li key={root}><code>{root}</code> <button type="button" className="qchat-reject" disabled={busy} onClick={()=>void save(roots.filter((r)=>r!==root))}>Remove</button></li>)}</ul>:<p className="empty-copy">Agents work in a copy of your repo that only has tracked files. Big gitignored datasets are invisible to them unless you list the folder here.</p>}
    <div className="chat-compose-row"><input aria-label="Data folder path" value={adding} onChange={(event)=>setAdding(event.target.value)} placeholder="/home/you/code/project/data" style={{flex:1,padding:"8px 10px",border:"1px solid #d6ddd2",borderRadius:8,font:"inherit"}}/><button className="button secondary" type="button" disabled={busy||!adding.trim()||roots.length>=5} onClick={()=>void save([...roots,adding.trim()])}>Add folder</button></div>
    {error&&<div className="chat-error" role="alert">{error}</div>}
    <p className="form-hint">Agents may read these folders to do their tasks (their contents can be sent to the model provider as they read). They are told never to change or delete anything in them and to write only in their own workspace. Must be inside your home folder.</p></section>;
}

const MEMORY_LABELS: Record<string,string> = { vision: "Vision", plan: "Plan & phases", crucial: "Crucial (you flagged)", decisions: "Decisions", definitions: "Definitions", constraints: "Constraints & preferences", deferred: "Postponed", open_questions: "Still open", corrections: "Changed your mind" };
function MemoryCard({memory,onCorrect}:{memory:Memory;onCorrect:()=>void}) {
  const sections = Object.entries(memory.memory).filter(([,items])=>items.length>0);
  const pending = memory.notYetMergedNotes;
  const logs = memory.detailedLogs || [];
  if (!sections.length && !pending.length && !logs.length) return null;
  return <section className="goal-card memory-card"><div className="goal-card-heading"><div><div className="eyebrow">WHAT THE VP REMEMBERS</div><h3>Everything agents are told about what you said</h3></div><button className="button secondary" onClick={onCorrect}>Something wrong? Tell the VP</button></div>
    {sections.map(([key,items])=><div key={key} className="memory-section"><b>{MEMORY_LABELS[key]||key}</b><ul>{items.map((item,index)=><li key={`${key}-${index}`}>{item}</li>)}</ul></div>)}
    {logs.length>0&&<details className="memory-section"><summary><b>Logged details ({logs.length}) · full detail agents are given</b></summary>{logs.map((log,index)=><div key={`l-${index}`} className="memory-log"><b>{log.crucial?"★ ":""}{log.title}</b> <small>{log.kind.replaceAll("_"," ")}</small><p>{log.detail}</p></div>)}</details>}
    {pending.length>0&&<div className="memory-section"><b>Just noted (not yet merged)</b><ul>{pending.map((item,index)=><li key={`p-${index}`}>{item}</li>)}</ul></div>}
    <p className="form-hint">This is curated from your conversations: duplicates merged, things you changed your mind about replaced, chit-chat dropped. Correct anything by telling the VP in chat.</p></section>;
}

function ProjectChat({projectId,projectName}:{projectId:string;projectName:string}) {
  const [status,setStatus]=React.useState<{available:boolean;model:string;accountLabel:string;red?:{available:boolean;model:string}}|null>(null);
  const [mode,setMode]=React.useState<"blue"|"red">("blue");
  const [turns,setTurns]=React.useState<{role:"user"|"assistant";content:string}[]>([]);
  const [message,setMessage]=React.useState("");
  const [sending,setSending]=React.useState(false);
  const [chatError,setChatError]=React.useState("");
  React.useEffect(()=>{let active=true;setTurns([]);setStatus(null);setChatError("");void request<{turns:Turn[]}>(`/api/v1/projects/${encodeURIComponent(projectId)}/chat/history`).then((h)=>{if(active)setTurns(h.turns);}).catch(()=>{});void request<typeof status>(`/api/v1/projects/${encodeURIComponent(projectId)}/chat/status`).then((value)=>{if(active)setStatus(value);}).catch((e)=>{if(active)setChatError(e instanceof Error?e.message:"Could not check the local Codex connection.");});return()=>{active=false;};},[projectId]);
  async function send(event:React.FormEvent<HTMLFormElement>){event.preventDefault();const text=message.trim();if(!text||sending||!(mode==="red"?status?.red?.available:status?.available))return;setTurns((current)=>[...current,{role:"user",content:text}]);setMessage("");setSending(true);setChatError("");try{const result=await request<{response:string}>(`/api/v1/projects/${encodeURIComponent(projectId)}/chat`,{method:"POST",body:JSON.stringify({message:text,history:[],mode})});setTurns((current)=>[...current,{role:"assistant",content:result.response}]);}catch(e){setChatError(e instanceof Error?e.message:"Codex chat request failed.");}finally{setSending(false);}}
  return <div className="chat-page"><section className="chat-heading"><div className="eyebrow">PROJECT CONVERSATION</div><h2>Talk through {projectName}</h2><p>The VP knows this project’s goals, your recorded answers, the board, and agent reports, and can read and search the project’s repository to answer. It cannot change files, create tasks, or start work.</p></section>
    <div className="chat-mode" role="group" aria-label="VP model"><button type="button" className={`button ${mode==="blue"?"primary":"secondary"}`} onClick={()=>setMode("blue")}>Blue · Codex VP</button><button type="button" className={`button ${mode==="red"?"primary":"secondary"}`} disabled={!status?.red?.available} title={status?.red?.available?"Local uncensored model on this machine":"Set NORTHSTAR_RED_ENDPOINT and NORTHSTAR_RED_MODEL to enable"} onClick={()=>setMode("red")}>Red · local model</button></div><section className="chat-connection"><div className="chat-agent-mark"><MessageSquare size={18}/></div><div><div className="eyebrow">SERVER-SIDE CODEX CONNECTION</div><h3>{status?.available?"Pi Codex auth file detected":status?"Pi Codex sign-in unavailable":"Checking local Pi setup…"}</h3><p>{status?.available?`${status.accountLabel} · ${status.model}. No account switching.`:"Start Pi and sign in to OpenAI Codex on this machine. Credentials stay in Pi's local auth store; they are never sent to the browser."}</p></div></section>
    <div className="chat-data-notice"><b>Project context:</b> A bounded snapshot of the selected project's charter, work items, open questions, recorded decisions, and automation state is included with each message. Your message and recent chat turns also go to the active Codex sign-in and may use its provider usage. The VP may open and read repository files itself (read-only) when your question needs it; nothing is modified. Every message is saved in the project’s permanent history, so reloading or a very long chat loses nothing; older turns are folded into a saved summary and the VP always gets a fresh, bounded context.</div>
    <div className="chat-transcript" aria-live="polite">{turns.map((turn,index)=><article className={`chat-turn ${turn.role}`} key={`${index}-${turn.role}`}><b>{turn.role==="user"?"You":"VP"}</b><p>{turn.content}</p></article>)}{sending&&<p className="chat-thinking">The VP is responding…</p>}</div>
    {chatError&&<div className="chat-error" role="alert">{chatError}</div>}
    <form className="chat-composer" onSubmit={(event)=>void send(event)}><div className="chat-compose-row"><textarea aria-label="Chat message" value={message} onChange={(event)=>setMessage(event.target.value)} maxLength={4000} disabled={!(mode==="red"?status?.red?.available:status?.available)||sending} placeholder={status?.available?"Write a message to Codex…":"Chat unavailable until Pi Codex sign-in is detected"}/><button className="button primary" disabled={!(mode==="red"?status?.red?.available:status?.available)||sending||!message.trim()}>{sending?"Sending…":"Send"}</button></div></form>
  </div>;
}

function InboxView({projectId,questions,selected,choose,recorded}:{projectId:string;questions:Question[];selected:QuestionDetail|null;choose:(id:string)=>void;recorded:()=>Promise<void>}) {
  return <div className="inbox-layout"><section className="inbox-questions"><div className="inbox-heading"><span>OPEN DECISIONS</span><span>{questions.length}</span></div>{questions.length ? questions.map((question)=><button key={question.questionId} className={`question-link ${selected?.questionId===question.questionId?"chosen":""}`} onClick={()=>choose(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking?"Your answer is needed to continue":"May affect project direction"}</small></span></button>) : <div className="inbox-empty"><Inbox size={19}/><b>Nothing needs your attention</b><small>No open project decisions.</small></div>}</section><section className="inbox-detail">{selected ? <QuestionChat key={selected.questionId} projectId={projectId} question={selected} recorded={recorded}/> : <div className="inbox-empty"><Inbox size={19}/><b>Select a decision</b><small>The VP will ask you here, like a chat.</small></div>}</section></div>;
}

function QuestionChat({projectId,question,recorded}:{projectId:string;question:QuestionDetail;recorded:()=>Promise<void>}) {
  const base=`/api/v1/projects/${encodeURIComponent(projectId)}/inbox/${encodeURIComponent(question.questionId)}`;
  const [turns,setTurns]=React.useState<Turn[]>([]);
  const [text,setText]=React.useState("");
  const [sending,setSending]=React.useState(false);
  const [proposal,setProposal]=React.useState<{text:string;selectedOptionId:string|null;settled?:boolean;editedByOwner?:boolean}|null>(null);
  const [editing,setEditing]=React.useState(false);
  const [editText,setEditText]=React.useState("");
  const [replaceMode,setReplaceMode]=React.useState<{optionId:string;label:string}|null>(null);
  const [logs,setLogs]=React.useState<Log[]>([]);
  const [editingLog,setEditingLog]=React.useState<{id:string;title:string;detail:string;kind:string;crucial:boolean}|null>(null);
  const [confirmRemove,setConfirmRemove]=React.useState("");
  const [rejecting,setRejecting]=React.useState(false);
  const [rejectReason,setRejectReason]=React.useState("");
  const [error,setError]=React.useState("");
  const isAlignment=question.questionId.startsWith("question.alignment.");
  React.useEffect(()=>{let active=true;void request<{turns:Turn[];logs?:Log[];draft:{text:string;selectedOptionId:string|null;settled?:boolean}|null}>(`/api/v1/projects/${encodeURIComponent(projectId)}/chat/history?questionId=${encodeURIComponent(question.questionId)}`).then(async(h)=>{if(!active)return;setTurns(h.turns);setLogs(h.logs||[]);if(h.draft){setProposal(h.draft);}else if(h.turns.some((t)=>t.role==="user")){try{const r=await request<{draft:{text:string;selectedOptionId:string|null;settled?:boolean}|null}>(`${base}/draft`,{method:"POST",body:JSON.stringify({})});if(active&&r.draft)setProposal(r.draft);}catch{/* the draft is a convenience; the chat still works */}}}).catch(()=>{});return()=>{active=false;};},[projectId,question.questionId]);
  async function send(event:React.FormEvent){event.preventDefault();const message=text.trim();if(!message||sending)return;setError("");
    if(replaceMode){setProposal({text:`${replaceMode.label}\n\nNote: ${message}`,selectedOptionId:replaceMode.optionId,settled:true});setReplaceMode(null);setText("");return;}
    setTurns((t)=>[...t,{role:"user",content:message}]);setText("");setSending(true);setProposal(null);
    try{const r=await request<{reply:string;settled?:boolean;logs?:Log[];proposedAnswer:{text:string;selectedOptionId:string|null}|null}>(`${base}/conversation`,{method:"POST",body:JSON.stringify({message})});setTurns((t)=>[...t,{role:"assistant",content:r.reply}]);if(r.logs?.length)setLogs((l)=>[...l,...r.logs!]);if(r.proposedAnswer)setProposal({...r.proposedAnswer,settled:Boolean(r.settled)});}
    catch(e){setError(e instanceof Error?e.message:"The VP could not reply.");}finally{setSending(false);}}
  function pick(option:{optionId:string;label:string}){setError("");if(option.optionId==="option.alignment.replace-goal"){setReplaceMode({optionId:option.optionId,label:option.label});setProposal(null);return;}setProposal({text:option.label,selectedOptionId:option.optionId,settled:true});}
  async function record(){if(!proposal)return;setSending(true);setError("");try{await request(`${base}/answers`,{method:"POST",body:JSON.stringify({expectedEtag:question.etag,text:proposal.text,...(proposal.selectedOptionId?{selectedOptionId:proposal.selectedOptionId}:{})})});await recorded();}catch(e){setError(e instanceof Error?e.message:"Could not record the answer.");setSending(false);}}
  async function reject(){const reason=rejectReason.trim();if(!reason){setError("Give a short reason for dismissing this question.");return;}setSending(true);try{await request(`${base}/closure`,{method:"POST",body:JSON.stringify({expectedEtag:question.etag,newStatus:"rejected",reason})});await recorded();}catch(e){setError(e instanceof Error?e.message:"Could not reject.");setSending(false);}}
  const logUrl=(id:string)=>`/api/v1/projects/${encodeURIComponent(projectId)}/logs/${encodeURIComponent(id)}`;
  async function saveLog(){if(!editingLog)return;setError("");try{const r=await request<{log:Log}>(logUrl(editingLog.id),{method:"POST",body:JSON.stringify({title:editingLog.title,detail:editingLog.detail,kind:editingLog.kind,crucial:editingLog.crucial})});setLogs((l)=>l.map((x)=>x.id===r.log.id?{...x,...r.log}:x));setEditingLog(null);}catch(e){setError(e instanceof Error?e.message:"Could not save the log.");}}
  async function removeLog(id:string){if(confirmRemove!==id){setConfirmRemove(id);return;}setError("");try{await request(logUrl(id),{method:"POST",body:JSON.stringify({remove:true})});setLogs((l)=>l.filter((x)=>x.id!==id));setConfirmRemove("");}catch(e){setError(e instanceof Error?e.message:"Could not remove the log.");}}
  const NOTE="\n\nNote: ";
  function startEdit(){if(!proposal)return;const hasPrefix=Boolean(proposal.selectedOptionId&&proposal.text.includes(NOTE));setEditText(hasPrefix?proposal.text.split(NOTE).slice(1).join(NOTE):proposal.selectedOptionId?"":proposal.text);setEditing(true);}
  async function saveEdit(){if(!proposal)return;const body=editText.trim();const label=proposal.selectedOptionId?proposal.text.split(NOTE)[0]:"";const text=proposal.selectedOptionId?[label,body].filter(Boolean).join(NOTE):body;if(!text){setError("The answer can't be empty.");return;}setSending(true);setError("");try{const r=await request<{draft:{text:string;selectedOptionId:string|null;settled?:boolean;editedByOwner?:boolean}}>(`${base}/draft`,{method:"POST",body:JSON.stringify({text,...(proposal.selectedOptionId?{selectedOptionId:proposal.selectedOptionId}:{})})});setProposal(r.draft);setEditing(false);}catch(e){setError(e instanceof Error?e.message:"Could not save your edit.");}finally{setSending(false);}}
  const options=question.record.options;
  return <div className="qchat">
    <div className="eyebrow">{question.blocking?"THE VP NEEDS YOUR INPUT":"THE VP ASKS"}</div>
    <article className="chat-turn assistant qchat-question"><b>VP</b><h2>{question.questionText}</h2>{isAlignment&&<details><summary>What I saw</summary><p className="alignment-evidence">{question.record.impact}</p></details>}{!isAlignment&&question.record.impact&&<p>{question.record.impact}</p>}{question.affectedWork.length>0&&<details className="affected-details"><summary>Why this matters <span>{question.affectedWork.length} affected planned item{question.affectedWork.length===1?"":"s"}</span></summary><div>{question.affectedWork.map((item)=><span key={item.workId}>{item.title}</span>)}</div></details>}</article>
    {options.length>0&&<div className="qchat-chips" role="group" aria-label="Quick answers">{options.map((option)=><button key={option.optionId} type="button" className="qchat-chip" title={option.consequence||""} onClick={()=>pick(option)}><b>{option.label}</b>{option.consequence&&<small>{option.consequence}</small>}</button>)}<span className="qchat-or">or type something below</span></div>}
    <div className="question-chat-transcript" aria-live="polite">{turns.map((turn,index)=><article className={`chat-turn ${turn.role}`} key={`${index}-${turn.role}`}><b>{turn.role==="user"?"You":"VP"}</b><p>{turn.content}</p></article>)}{sending&&!proposal&&<p className="chat-thinking">The VP is replying…</p>}</div>
    {logs.length>0&&<section className="qchat-logs" aria-label="Logged from your messages"><div className="eyebrow">LOGGED FROM YOUR MESSAGES · {logs.length}</div><p className="form-hint">Already saved, nothing to confirm. Fix or remove anything that’s off; the VP and every agent get these in full.</p>{logs.map((log)=><article key={log.id} className={`qchat-log ${log.crucial?"crucial":""}`}>{editingLog?.id===log.id?<><input aria-label="Log title" value={editingLog.title} onChange={(event)=>setEditingLog({...editingLog,title:event.target.value})} maxLength={120}/><textarea aria-label="Log detail" className="qchat-edit" rows={5} value={editingLog.detail} onChange={(event)=>setEditingLog({...editingLog,detail:event.target.value})} maxLength={1500}/><label className="qchat-log-crucial"><input type="checkbox" checked={editingLog.crucial} onChange={(event)=>setEditingLog({...editingLog,crucial:event.target.checked})}/> Crucial</label><div className="form-actions"><button className="button secondary" type="button" onClick={()=>setEditingLog(null)}>Cancel</button><button className="button primary" type="button" disabled={!editingLog.title.trim()||!editingLog.detail.trim()} onClick={()=>void saveLog()}>Save</button></div></>:<><div className="qchat-log-head"><span className="qchat-log-kind">{log.kind.replaceAll("_"," ")}</span><b>{log.crucial?"★ ":""}{log.title}</b>{log.edited&&<small>edited</small>}</div><p>{log.detail}</p><div className="qchat-log-actions"><button type="button" onClick={()=>{setConfirmRemove("");setEditingLog({id:log.id,title:log.title,detail:log.detail,kind:log.kind,crucial:log.crucial});}}>Edit</button><button type="button" onClick={()=>void removeLog(log.id)}>{confirmRemove===log.id?"Really remove?":"Remove"}</button></div></>}</article>)}</section>}
    {proposal&&<div className={`qchat-proposal ${proposal.settled?"":"tentative"}`} role="group" aria-label="Confirm answer"><div className="eyebrow">{editing?"EDIT WHAT GETS RECORDED":proposal.editedByOwner?"YOUR EDIT · RECORD THIS?":proposal.settled?"RECORD THIS AS YOUR ANSWER?":"WHAT I’D RECORD SO FAR · NOT FINAL"}</div>{editing?<><textarea className="qchat-edit" aria-label="Edit the answer" value={editText} onChange={(event)=>setEditText(event.target.value)} rows={7} maxLength={9000}/>{proposal.selectedOptionId&&<p className="form-hint">Your chosen option stays: {proposal.text.split(NOTE)[0]}. Edit the note beneath it.</p>}<div className="form-actions"><button className="button secondary" type="button" disabled={sending} onClick={()=>setEditing(false)}>Cancel</button><button className="button primary" type="button" disabled={sending||!editText.trim()&&!proposal.selectedOptionId} onClick={()=>void saveEdit()}>{sending?"Saving…":"Use my edit"}</button></div></>:<><p>{proposal.text}</p><div className="form-actions"><button className="button secondary" type="button" disabled={sending} onClick={startEdit}>Edit</button><button className="button secondary" type="button" disabled={sending} onClick={()=>setProposal(null)}>{proposal.settled?"Not yet":"Keep talking"}</button><button className="button primary" type="button" disabled={sending} onClick={()=>void record()}>{sending?"Recording…":proposal.settled?"Record answer":"Record this now"}</button></div></>}</div>}
    {error&&<div className="chat-error" role="alert">{error}</div>}
    <form className="chat-composer" onSubmit={(event)=>void send(event)}><div className="chat-compose-row"><textarea aria-label="Reply to the VP" value={text} onChange={(event)=>setText(event.target.value)} maxLength={4000} disabled={sending} placeholder={replaceMode?"Type the full replacement goal…":"Answer, or ask a btw — I can read the repo to answer…"} onKeyDown={(event)=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();void send(event as unknown as React.FormEvent);}}}/><button className="button primary" disabled={sending||!text.trim()}>{sending?"…":"Send"}</button></div>{!isAlignment&&!rejecting&&<button type="button" className="qchat-reject" disabled={sending} onClick={()=>setRejecting(true)}>Dismiss this question without answering…</button>}</form>
    {!isAlignment&&rejecting&&<div className="qchat-proposal tentative" role="group" aria-label="Dismiss question"><div className="eyebrow">DISMISS WITHOUT ANSWERING</div><p>This closes the question and does <b>not</b> record an answer. If you meant to answer it, cancel and use the reply box above.</p>{text.trim()&&<p className="form-hint">Your unsent reply (“{text.trim().slice(0,80)}{text.trim().length>80?"…":""}”) is not part of this; it stays in the reply box.</p>}<textarea className="qchat-edit" aria-label="Why dismiss" value={rejectReason} onChange={(event)=>setRejectReason(event.target.value)} rows={3} placeholder="Why is this question not worth answering?"/><div className="form-actions"><button className="button secondary" type="button" disabled={sending} onClick={()=>{setRejecting(false);setRejectReason("");}}>Cancel</button><button className="button primary" type="button" disabled={sending||!rejectReason.trim()} onClick={()=>void reject()}>Dismiss question</button></div></div>}
    <p className="form-hint">Everything here is saved. Side questions don’t record an answer; useful things you say are kept as notes the VP and its agents use later. An answer is only recorded when you confirm it.</p>
  </div>;
}

function RepoInspectionPreview({report}:{report:RepoReport}) {
  return <div className="repo-inspection-preview"><div className="repo-facts"><b>{report.repositoryName}</b><span>{report.branch} · {report.head.slice(0,12)}</span><span>{report.trackedFileCount} eligible tracked paths · working-tree status not inspected</span></div><div className="eyebrow">WHAT I FOUND IN TRACKED DOCS</div>{report.topLevelAreas.length>0&&<p><b>Top-level areas:</b> {report.topLevelAreas.join(", ")}</p>}{report.documents.map((doc)=><details key={doc.path} className="repo-doc"><summary>{doc.path} · {doc.title}</summary><p>{doc.excerpt}</p></details>)}{!report.documents.length&&<p>No eligible README/context/docs were found; the interview will treat that as unknown rather than inventing a project goal.</p>}<p className="inspection-policy">{report.inspectionPolicy}</p></div>;
}

function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:React.ReactNode}) { return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17}/></button></div>{children}</section></div>; }

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
