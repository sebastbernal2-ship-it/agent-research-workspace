import React from "react";
import { createRoot } from "react-dom/client";
import { Brain, ChevronDown, Folder, House, Inbox, Layers3, MessageSquare, Plus, Settings, X } from "lucide-react";
import "./style.css";
import "./goal.css";
import "./work-views.css";
import "./kanban-board.css";
import "./chat.css";
import "./home.css";

type Project = { projectId: string; name: string; status: string; charter: { revision: number; goals: string[]; nonGoals: string[]; acceptanceCriteria: string[] } };
type Work = { workId: string; title: string; objective: string; workType: string; status: string; parentWorkId?: string | null; dependsOn: string[]; acceptanceCriteria: string[]; revision: number };
type ProjectEvent = { eventId: string; title: string; description: string; actorLabel: string; occurredAt: string };
type AlignmentSegment = { index: number; title: string; prompt: string; evidence: string; status: string; answer: { text: string } | null };
type Alignment = { status: string; repositoryName: string | null; branch: string | null; head: string | null; segments: AlignmentSegment[] };
type Activity = { id: string; workId: string; workTitle: string; actor: string; text: string; at: string };
type Automation = { autoRun: boolean; workerAvailable: boolean; demoProject: boolean };
type Log = { id: string; kind: string; title: string; detail: string; crucial: boolean; edited?: boolean };
type Memory = { memory: Record<string, string[]>; detailedLogs?: Log[]; notYetMergedNotes: string[] };
type Summary = { phases?: Phase[]; dataRoots?: string[]; memory?: Memory; automation: Automation; activity: Activity[]; drafting: boolean; project: Project; workItems: Work[]; recentEvents: ProjectEvent[]; counts: Record<string, number>; alignment?: Alignment | null };
type RepoReport = { repositoryName: string; repositoryPath: string; head: string; branch: string; trackedFileCount: number; trackedChanges: number | null; topLevelAreas: string[]; documents: { path: string; title: string; excerpt: string }[]; goalHint: string; fingerprint: string; inspectionPolicy: string };
type ProjectMode = "choose" | "new" | "existing";
type Question = { questionId: string; questionType: string; questionText: string; priority: string; blocking: boolean; workIds: string[]; etag: string };
type QuestionDetail = Question & { record: { questionType: string; options: { optionId: string; label: string; consequence?: string }[]; impact: string; status: string; questionText: string }; affectedWork: { workId: string; title: string; status: string }[] };
type Turn = { role: "user" | "assistant"; content: string };
type View = "home" | "phases" | "memory" | "ask" | "activity";
type Phase = { phaseId: string; index: number; title: string; goal: string; status: string; revision: number; exitCriteria: string[]; assessment: { criterion: string; met: boolean; evidence: string }[]; caveats: string[]; steers: number; tasks: { total: number; done: number; running: number; failed: number }; improvements: { open: number; done: number; titles: string[] } };

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


const questionRank = (q: Question) => (q.questionId.startsWith("question.phase") ? 0 : q.blocking ? 1 : 2);
const firstLine = (text: string) => (text.split("\n").map((l) => l.trim()).filter(Boolean)[0] || text).replace(/^(\d+ of \d+ · |Follow-up · )/, "");
const excerpt = (text: string) => { const done = text.split("\n").find((l) => l.trim().toUpperCase().startsWith("DONE:")); return (done ? done.replace(/^DONE:\s*/i, "") : text.replace(/\s+/g, " ")).slice(0, 170); };
const ago = (iso: string) => { const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000); return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };

function App() {
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [projectId, setProjectId] = React.useState(() => new URLSearchParams(window.location.search).get("project") || "project.northstar");
  const [summary, setSummary] = React.useState<Summary | null>(null);
  const [view, setView] = React.useState<View>("home");
  const [projectsOpen, setProjectsOpen] = React.useState(false);
  const [questions, setQuestions] = React.useState<Question[]>([]);
  const [selectedQuestion, setSelectedQuestion] = React.useState<QuestionDetail | null>(null);
  const selectedIdRef = React.useRef("");
  const [error, setError] = React.useState("");
  const [projectDialog, setProjectDialog] = React.useState(false);
  const [projectMode, setProjectMode] = React.useState<ProjectMode>("choose");
  const [repositoryPath, setRepositoryPath] = React.useState("");
  const [repoReport, setRepoReport] = React.useState<RepoReport | null>(null);
  const [draftName, setDraftName] = React.useState("");
  const [draftGoal, setDraftGoal] = React.useState("");
  const [inspectionBusy, setInspectionBusy] = React.useState(false);
  const [workDialog, setWorkDialog] = React.useState(false);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [chatOpen, setChatOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const loadProjects = React.useCallback(async () => {
    const result = await request<{items: Project[]}>('/api/v1/projects');
    setProjects(result.items);
    return result.items;
  }, []);

  // Keeps the question you are on; only falls back to the most urgent one when it is gone (answered/closed).
  const loadInbox = React.useCallback(async (id: string) => {
    await request(`/api/v1/projects/${encodeURIComponent(id)}/alignment/resume`, { method: "POST", body: JSON.stringify({}) });
    const result = await request<{questions: Question[]}>(`/api/v1/projects/${encodeURIComponent(id)}/inbox?status=open`);
    setQuestions(result.questions);
    if (!result.questions.length) { selectedIdRef.current = ""; setSelectedQuestion(null); return result.questions; }
    const keep = result.questions.some((q) => q.questionId === selectedIdRef.current) ? selectedIdRef.current : "";
    const target = keep || [...result.questions].sort((a, b) => questionRank(a) - questionRank(b))[0].questionId;
    const detail = await request<QuestionDetail>(`/api/v1/projects/${encodeURIComponent(id)}/inbox/${encodeURIComponent(target)}`);
    selectedIdRef.current = detail.questionId;
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
    const live = summary && !summary.automation.demoProject && (summary.drafting || summary.automation.autoRun
      || summary.workItems.some((w) => ["running", "verifying", "ready"].includes(w.status))
      || (summary.phases || []).some((p) => ["running", "verifying", "ready"].includes(p.status))
      || (summary.alignment?.status === "completed" && !summary.workItems.some((w) => w.workId !== "work.alignment-intake")));
    if (!live) return;
    const timer = window.setInterval(() => { loadProject(projectId).catch(() => {}); loadInbox(projectId).catch(() => {}); }, 4000);
    return () => window.clearInterval(timer);
  }, [summary, projectId]);

  async function post(path: string, body: unknown, fail: string) {
    setBusy(true);
    try { await request(`/api/v1/projects/${encodeURIComponent(projectId)}/${path}`, { method: "POST", body: JSON.stringify(body) }); await loadProject(projectId); return true; }
    catch (e) { setError(e instanceof Error ? e.message : fail); return false; }
    finally { setBusy(false); }
  }
  const decidePhase = (phaseId: string, moveOn: boolean) => post(`phases/${encodeURIComponent(phaseId)}/decision`, { moveOn }, "Could not update the phase.");
  const planPhases = async () => { if (await post("phases/plan", {}, "The VP could not plan phases.")) setView("phases"); };
  const setAutoRun = (enabled: boolean) => post("automation", { autoRun: enabled }, "Could not change auto-run.");
  const decideWork = (workId: string, revision: number, decision: "approve" | "reject" | "retry") => post(`work/${encodeURIComponent(workId)}/approval`, { expectedRevision: revision, decision }, "Could not record the work approval.");

  const project = summary?.project;
  const phases = summary?.phases || [];
  const workItems = summary?.workItems || [];
  const tasks = workItems.filter((w) => !w.workId.startsWith("work.phase-") && w.workId !== "work.alignment-intake");
  const demo = Boolean(summary?.automation.demoProject);
  const needs = [...questions].sort((a, b) => questionRank(a) - questionRank(b));
  const alignmentWork = workItems.find((item) => item.workId === "work.alignment-intake");
  const alignmentCompleted = alignmentWork?.status === "completed";
  const alignmentPending = questions.some((q) => q.questionId.startsWith("question.alignment.")) || Boolean(alignmentWork && !["completed", "cancelled"].includes(alignmentWork.status));
  const activePhase = phases.find((p) => p.status === "verifying") || phases.find((p) => p.status === "running") || phases.find((p) => p.status === "ready");
  const running = tasks.filter((w) => ["running", "verifying"].includes(w.status));
  const reviewing = phases.find((p) => p.status === "verifying");
  const statusLine = reviewing ? `Phase ${reviewing.index} is done and waiting for your review`
    : needs.some((q) => q.blocking) ? "Waiting on your answer"
    : alignmentPending ? "Alignment interview in progress"
    : running.length ? `Working on: ${running[0].title}${running.length > 1 ? ` +${running.length - 1} more` : ""}`
    : activePhase ? `Phase ${activePhase.index} in progress`
    : phases.length === 0 && !demo ? "No plan yet" : "Idle";
  const titles: Record<View, string> = { home: project?.name || "Project", phases: "Phases", memory: "What the VP remembers", ask: selectedQuestion?.questionId.startsWith("question.alignment.") ? "Alignment interview" : "Needs you", activity: "Activity" };

  function openProjectDialog() { setProjectMode("choose"); setRepositoryPath(""); setRepoReport(null); setDraftName(""); setDraftGoal(""); setProjectDialog(true); }

  async function inspectRepository() {
    if (!repositoryPath.trim()) { setError("Choose a local Git repository path first."); return; }
    setInspectionBusy(true); setError("");
    try {
      const result = await request<{report: RepoReport}>("/api/v1/repositories/inspect", { method: "POST", body: JSON.stringify({ repositoryPath: repositoryPath.trim() }) });
      setRepoReport(result.report); setDraftName(result.report.repositoryName); setDraftGoal(result.report.goalHint || "");
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
      selectedIdRef.current = "";
      await loadProjects(); await loadProject(result.project.projectId); await loadInbox(result.project.projectId);
      setView("ask"); setProjectDialog(false);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create project."); }
    finally { setBusy(false); }
  }

  async function createWork(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!project) return; setBusy(true);
    const form = event.currentTarget; const data = new FormData(form);
    try {
      const acceptanceCriteria = String(data.get('acceptanceCriteria') || "").split("\n").map((line) => line.trim()).filter(Boolean);
      await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/work`, { method: 'POST', body: JSON.stringify({ title: data.get('title'), objective: data.get('objective'), workType: data.get('workType'), parentWorkId: data.get('parentWorkId') || null, acceptanceCriteria }) });
      await loadProject(project.projectId); setView("phases"); setWorkDialog(false); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not add planned work."); }
    finally { setBusy(false); }
  }

  async function openQuestion(questionId: string) {
    if (!project) return;
    try {
      const detail = await request<QuestionDetail>(`/api/v1/projects/${encodeURIComponent(project.projectId)}/inbox/${encodeURIComponent(questionId)}`);
      selectedIdRef.current = detail.questionId; setSelectedQuestion(detail); setView("ask");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load question."); }
  }

  async function questionRecorded() {
    if (!project || !selectedQuestion) return;
    const wasAlignment = selectedQuestion.questionId.startsWith("question.alignment.");
    await loadProject(project.projectId);
    const remaining = await loadInbox(project.projectId);
    if (wasAlignment && remaining.length === 0) setView("home");
  }

  async function switchProject(id: string) {
    try { selectedIdRef.current = ""; await loadProject(id); await loadInbox(id); setView("home"); setChatOpen(false); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the project."); }
  }

  return <div className="amp-shell">
    <aside className="amp-sidebar">
      <div className="amp-brand-row"><a className="amp-brand" href="#workspace"><span className="amp-logo"><Layers3 size={16}/></span><b>Northstar</b></a><button className="new-thread" aria-label="New project" title="New project" onClick={openProjectDialog}><Plus size={16}/></button></div>
      <button className="amp-project-picker" onClick={() => setProjectsOpen((open) => !open)}><Folder size={15}/><span>{project?.name || "Projects"}</span><ChevronDown size={13}/></button>
      {projectsOpen && <div className="project-menu">{projects.map((item)=><button key={item.projectId} className={item.projectId===projectId?"current":""} onClick={()=>{void switchProject(item.projectId);setProjectsOpen(false);}}>{item.name}</button>)}<button onClick={()=>{openProjectDialog();setProjectsOpen(false);}}><Plus size={13}/>New project</button></div>}
      <nav className="amp-sidebar-links" aria-label="Project navigation">
        <button onClick={() => setView("home")} className={view === "home" || view === "ask" || view === "activity" ? "current" : ""}><House size={15}/>Home <span className={needs.length ? "nav-alert" : ""}>{needs.length || ""}</span></button>
        <button onClick={() => setView("phases")} className={view === "phases" ? "current" : ""}><Layers3 size={15}/>Phases <span className={reviewing ? "nav-alert" : ""}>{reviewing ? "review" : phases.length || ""}</span></button>
        <button onClick={() => setView("memory")} className={view === "memory" ? "current" : ""}><Brain size={15}/>Memory</button>
      </nav>
      <div className="sidebar-spacer"/>
      <div className="amp-sidebar-links"><button onClick={() => setSettingsOpen(true)}><Settings size={15}/>Settings</button></div>
    </aside>
    <main className="goal-main">
      <header className="goal-topbar"><div className="goal-topbar-title"><span className="project-dot"/><div><h1>{titles[view]}</h1><small>{statusLine}</small></div></div>
        <div className="goal-topbar-actions">
          <span className="auto-run-status"><span>Full auto</span><button role="switch" aria-checked={Boolean(summary?.automation.autoRun)} disabled={busy || !summary || demo || !summary.automation.workerAvailable} title={demo ? "The synthetic demo project cannot run agents." : !summary?.automation.workerAvailable ? "No worker CLI (Claude Code or Codex) is available." : "On: the VP accepts its own plan and moves from phase to phase without stopping (you can read what happened in Latest). Off: you accept or change the plan and review each finished phase."} onClick={() => void setAutoRun(!summary?.automation.autoRun)}><i/>{summary?.automation.autoRun ? "On" : "Off"}</button></span>
          <button className="button secondary" onClick={() => setChatOpen((open) => !open)}><MessageSquare size={14}/>Ask the VP</button>
        </div></header>
      <div className="goal-stage">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!project ? <div className="loading">Loading your project…</div>
          : view === "ask" ? <InboxView projectId={project.projectId} questions={needs} selected={selectedQuestion} choose={openQuestion} recorded={questionRecorded}/>
          : view === "phases" ? <PhasesPage phases={phases} items={workItems} tasks={tasks} busy={busy} approve={decideWork} decide={decidePhase} plan={planPhases} review={() => setView("ask")} alignmentCompleted={alignmentCompleted} demo={demo} addTask={() => setWorkDialog(true)}/>
          : view === "memory" ? <MemoryPage memory={summary?.memory} alignment={summary?.alignment} onCorrect={() => setChatOpen(true)}/>
          : view === "activity" ? <div className="goal-scroll"><button className="back-link" onClick={() => setView("home")}>← Home</button><ActivityView items={summary?.activity || []} running={running.length} workItems={tasks}/></div>
          : <Home goal={project.charter.goals.join(" ")} needs={needs} open={openQuestion} activePhase={activePhase} running={running} latest={summary?.activity || []} plan={planPhases} busy={busy} demo={demo} alignmentPending={alignmentPending} alignmentCompleted={alignmentCompleted} hasPhases={phases.length > 0} goPhases={() => setView("phases")} goActivity={() => setView("activity")} goAsk={() => setView("ask")}/>}
      </div>
    </main>
    {chatOpen && project && <ChatDrawer projectId={project.projectId} onClose={() => setChatOpen(false)}/>}
    {settingsOpen && project && <Modal title="Project settings" onClose={() => setSettingsOpen(false)}>
      {!demo ? <DataRootsCard projectId={project.projectId} roots={summary?.dataRoots || []} reload={() => loadProject(project.projectId)}/> : <p className="empty-copy">The synthetic demo project has no settings.</p>}
      <p className="form-hint">Agents run as local Codex or Claude Code workers in a copy of your repository; nothing is pushed anywhere. This is a local prototype: no sign-in, loopback only.</p>
    </Modal>}

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

function Home({goal,needs,open,activePhase,running,latest,plan,busy,demo,alignmentPending,alignmentCompleted,hasPhases,goPhases,goActivity,goAsk}:{goal:string;needs:Question[];open:(id:string)=>void;activePhase?:Phase;running:Work[];latest:Activity[];plan:()=>void;busy:boolean;demo:boolean;alignmentPending:boolean;alignmentCompleted:boolean;hasPhases:boolean;goPhases:()=>void;goActivity:()=>void;goAsk:()=>void}) {
  const pct = activePhase && activePhase.tasks.total ? Math.round(100 * activePhase.tasks.done / activePhase.tasks.total) : 0;
  const unmet = activePhase ? activePhase.assessment.filter((a) => !a.met).length : 0;
  const reports = latest.filter((a) => !/^(Committed |No files changed|The app stopped)/.test(a.text.trim()));  // housekeeping notes aren't news
  return <div className="home">
    {goal && <p className="home-goal">{goal}</p>}
    <section className={`home-card ${needs.length ? "needs" : ""}`} aria-labelledby="needs-title">
      <h2 id="needs-title">{needs.length ? `Needs you · ${needs.length}` : "Needs you"}</h2>
      {needs.length ? <>{needs.slice(0, 5).map((q) => <button key={q.questionId} className="needs-row" onClick={() => open(q.questionId)}><span className={`question-dot ${q.priority}`}/><span><b>{firstLine(q.questionText).slice(0, 140)}</b><small>{q.questionId.startsWith("question.phaseplan-") ? "Plan ready · accept it or tell the VP what to change" : q.questionId.startsWith("question.phase-") ? "Phase checkpoint · review what was built" : q.blocking ? "Needed to continue" : "When you have a moment"}</small></span><span aria-hidden="true">→</span></button>)}{needs.length > 5 && <button className="link-button" onClick={goAsk}>and {needs.length - 5} more</button>}</>
        : <p className="empty-copy">Nothing needs you. The VP will tell you here when it does.</p>}
    </section>
    <section className="home-card" aria-labelledby="now-title">
      <h2 id="now-title">Now</h2>
      {activePhase ? <>
        <div className="eyebrow">PHASE {activePhase.index} · {PHASE_STATUS[activePhase.status] || activePhase.status}</div>
        <h3>{activePhase.title}</h3>
        <div className="phase-progress" aria-label="Task progress"><span style={{width: `${pct}%`}}/></div>
        <small>{activePhase.tasks.done} of {activePhase.tasks.total} tasks done{unmet > 0 && activePhase.status === "running" ? ` · ${unmet} exit criteri${unmet === 1 ? "on" : "a"} still open` : ""}</small>
        {running.length > 0 && <p className="home-working"><b>Working on:</b> {running.map((w) => w.title).join(" · ")}</p>}
        <button className="link-button" onClick={goPhases}>Open the phase →</button>
      </> : alignmentPending ? <><p className="empty-copy">The VP is interviewing you. When you finish, it plans the project into phases and gets to work.</p><button className="button primary" onClick={goAsk}>Continue the interview</button></>
        : !hasPhases && alignmentCompleted && !demo ? <><p className="empty-copy">The VP is planning the project into phases from your answers. This takes a minute or two and then shows up under “Needs you” to accept or change.</p><button className="link-button" disabled={busy} onClick={plan}>Plan now</button></>
        : <p className="empty-copy">No active phase.</p>}
    </section>
    <section className="home-card" aria-labelledby="latest-title">
      <h2 id="latest-title">Latest</h2>
      {reports.length ? <><ul className="latest-list">{reports.slice(0, 5).map((a) => <li key={a.id}><div><b>{a.workTitle}</b><time>{ago(a.at)}</time></div><span>{excerpt(a.text)}</span></li>)}</ul><button className="link-button" onClick={goActivity}>All activity →</button></>
        : <p className="empty-copy">Nothing yet. Reports from the agents appear here as work finishes.</p>}
    </section>
  </div>;
}

function PhasesPage({phases,items,tasks,busy,approve,decide,plan,review,alignmentCompleted,demo,addTask}:{phases:Phase[];items:Work[];tasks:Work[];busy:boolean;approve:(workId:string,revision:number,decision:"approve"|"reject"|"retry")=>Promise<boolean>;decide:(phaseId:string,moveOn:boolean)=>unknown;plan:()=>void;review:()=>void;alignmentCompleted:boolean;demo:boolean;addTask:()=>void}) {
  const [board, setBoard] = React.useState(false);
  return <div className="goal-scroll">
    <div className="seg-row"><div className="seg" role="group" aria-label="View"><button className={!board ? "on" : ""} onClick={() => setBoard(false)}>Phases</button><button className={board ? "on" : ""} onClick={() => setBoard(true)}>Board</button></div>{!demo && <button className="button secondary" onClick={addTask}><Plus size={14}/>Add task</button>}</div>
    {board ? <KanbanBoard items={tasks} busy={busy} decide={approve}/> : <PhasesView phases={phases} items={items} busy={busy} approve={approve} decide={decide} plan={plan} review={review} alignmentCompleted={alignmentCompleted} demo={demo}/>}
  </div>;
}

function MemoryPage({memory,alignment,onCorrect}:{memory?:Memory;alignment?:Alignment|null;onCorrect:()=>void}) {
  return <div className="goal-scroll">
    <MemoryCard memory={memory || { memory: {}, notYetMergedNotes: [] }} onCorrect={onCorrect}/>
    {alignment && alignment.segments.length > 0 && <details className="goal-card"><summary><b>Interview answers</b> <small>{alignment.segments.filter((s) => s.status === "answered").length} of {alignment.segments.length} answered</small></summary>{alignment.segments.map((s) => <article className="alignment-record-segment" key={s.index}><b>{s.index}. {s.title}</b><p>{s.prompt}</p><p className="alignment-answer">{s.answer?.text || "Not answered yet."}</p></article>)}</details>}
  </div>;
}

function ChatDrawer({projectId,onClose}:{projectId:string;onClose:()=>void}) {
  const [status,setStatus]=React.useState<{available:boolean;red?:{available:boolean}}|null>(null);
  const [mode,setMode]=React.useState<"blue"|"red">("blue");
  const [turns,setTurns]=React.useState<Turn[]>([]);
  const [message,setMessage]=React.useState("");
  const [sending,setSending]=React.useState(false);
  const [chatError,setChatError]=React.useState("");
  const endRef=React.useRef<HTMLDivElement>(null);
  const usable=mode==="red"?Boolean(status?.red?.available):Boolean(status?.available);
  React.useEffect(()=>{let active=true;setTurns([]);setChatError("");void request<{turns:Turn[]}>(`/api/v1/projects/${encodeURIComponent(projectId)}/chat/history`).then((h)=>{if(active)setTurns(h.turns);}).catch(()=>{});void request<typeof status>(`/api/v1/projects/${encodeURIComponent(projectId)}/chat/status`).then((v)=>{if(active)setStatus(v);}).catch((e)=>{if(active)setChatError(e instanceof Error?e.message:"Could not reach the VP.");});return()=>{active=false;};},[projectId]);
  React.useEffect(()=>{endRef.current?.scrollIntoView?.({block:"end"});},[turns,sending]);
  async function send(event:React.FormEvent<HTMLFormElement>){event.preventDefault();const text=message.trim();if(!text||sending||!usable)return;setTurns((t)=>[...t,{role:"user",content:text}]);setMessage("");setSending(true);setChatError("");try{const r=await request<{response:string}>(`/api/v1/projects/${encodeURIComponent(projectId)}/chat`,{method:"POST",body:JSON.stringify({message:text,history:[],mode})});setTurns((t)=>[...t,{role:"assistant",content:r.response}]);}catch(e){setChatError(e instanceof Error?e.message:"The VP could not reply.");}finally{setSending(false);}}
  return <aside className="chat-drawer" aria-label="Ask the VP">
    <header><div><b>Ask the VP</b><small>Knows your answers, reads the repo, can’t change anything. Saved to the project record.</small></div><button className="icon-button" aria-label="Close" onClick={onClose}><X size={16}/></button></header>
    {status?.red?.available && <div className="chat-mode" role="group" aria-label="VP model"><button type="button" className={`button ${mode==="blue"?"primary":"secondary"}`} onClick={()=>setMode("blue")}>Codex</button><button type="button" className={`button ${mode==="red"?"primary":"secondary"}`} onClick={()=>setMode("red")}>Local model</button></div>}
    <div className="chat-transcript" aria-live="polite">{turns.length===0&&!sending&&<p className="empty-copy">Ask what was built, why, or what’s weak. It can read the repo to answer.</p>}{turns.map((turn,index)=><article className={`chat-turn ${turn.role}`} key={`${index}-${turn.role}`}><b>{turn.role==="user"?"You":"VP"}</b><p>{turn.content}</p></article>)}{sending&&<p className="chat-thinking">The VP is replying…</p>}<div ref={endRef}/></div>
    {chatError&&<div className="chat-error" role="alert">{chatError}</div>}
    <form className="chat-composer" onSubmit={(event)=>void send(event)}><div className="chat-compose-row"><textarea aria-label="Chat message" value={message} onChange={(event)=>setMessage(event.target.value)} maxLength={4000} disabled={!usable||sending} placeholder={usable?"Message the VP…":"The VP’s model isn’t available (is Codex signed in?)"} onKeyDown={(event)=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();void send(event as unknown as React.FormEvent<HTMLFormElement>);}}}/><button className="button primary" disabled={!usable||sending||!message.trim()}>Send</button></div></form>
  </aside>;
}

function KanbanBoard({items,busy,decide}:{items:Work[];busy:boolean;decide:(workId:string,revision:number,decision:"approve"|"reject"|"retry")=>Promise<boolean>}) {
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
    <section className="kanban-board" aria-label="Project work kanban board">{columns.map((column)=>{
      const cards=items.filter((item)=>column.statuses.includes(item.status));
      return <div className="kanban-column" key={column.id}><header><h3>{column.title}</h3><span>{cards.length}</span></header>{cards.length ? <div className="kanban-cards">{cards.map((item)=>{
        const parent=item.parentWorkId?byId.get(item.parentWorkId):undefined;
        return <button className="kanban-card" key={item.workId} onClick={()=>setSelectedWork(item)} aria-label={`Open ${item.title} details`}><div className="kanban-card-meta"><span className={`thread-status-dot ${item.status}`}/><span>{item.workType}</span><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div><h4>{item.title}</h4>{parent&&<small>Under: {parent.title}</small>}{item.status==="proposed"&&<span role="button" tabIndex={0} className="button primary kanban-approve" onClick={(event)=>{event.stopPropagation();void decide(item.workId,item.revision,"approve");}} onKeyDown={(event)=>{if(event.key==="Enter"){event.stopPropagation();void decide(item.workId,item.revision,"approve");}}}>Approve</span>}</button>;
      })}</div> : <p className="kanban-empty">Empty</p>}</div>;
    })}</section>
    {stopped.length>0&&<details className="stopped-items"><summary>Stopped · {stopped.length}</summary><div>{stopped.map((item)=><button key={item.workId} onClick={()=>setSelectedWork(item)}>{item.title}<span>{item.status}</span></button>)}</div></details>}
    {selectedWork&&<Modal title={selectedWork.title} onClose={()=>setSelectedWork(null)}><div className="work-detail-modal"><div className="work-detail-meta"><span className={`work-status ${selectedWork.status}`}><i/>{selectedWork.status.replaceAll("_"," ")}</span><span>{selectedWork.workType}</span>{selectedWork.parentWorkId&&<span>Under {byId.get(selectedWork.parentWorkId)?.title||selectedWork.parentWorkId}</span>}</div><p>{selectedWork.objective}</p>{selectedWork.acceptanceCriteria.length>0&&<div><b>Acceptance criteria</b><ul>{selectedWork.acceptanceCriteria.map((criterion,index)=><li key={`${index}-${criterion}`}>{criterion}</li>)}</ul></div>}{selectedWork.status==="proposed"?<><p className="form-hint">Approving makes this task ready; the VP's agents pick it up in dependency order, one at a time.</p><div className="form-actions"><button className="button secondary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"reject"))setSelectedWork(null);}}>Decline</button><button className="button primary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"approve"))setSelectedWork(null);}}>Approve task</button></div></>:selectedWork.status==="ready"?<p className="form-hint">Approved. It starts as soon as earlier tasks it depends on are done.</p>:selectedWork.status==="failed"?<><p className="form-hint">This task failed or was interrupted. The VP will see the failure and plan the fix, or you can retry it.</p><div className="form-actions"><button className="button primary" disabled={busy} onClick={async()=>{if(await decide(selectedWork.workId,selectedWork.revision,"retry"))setSelectedWork(null);}}>Retry task</button></div></>:null}</div></Modal>}
  </div>;
}

function ActivityView({items,running,workItems}:{items:Activity[];running:number;workItems:Work[]}) {
  const active = workItems.filter((w)=>["running","verifying"].includes(w.status));
  return <div className="goal-scroll"><section className="goal-card"><div className="goal-card-heading"><div><div className="eyebrow">RIGHT NOW</div><h3>{running ? `${running} task${running===1?"":"s"} in progress` : "Nothing running"}</h3></div></div>{active.length?<ul className="criteria-list">{active.map((w)=><li key={w.workId}><b>{w.title}</b> · {w.workType}</li>)}</ul>:<p className="empty-copy">Turn on auto-run, or approve work in Phases, and agent work shows up here as it happens.</p>}</section>
    <section className="goal-card"><div className="goal-card-heading"><div><div className="eyebrow">REPORTS</div><h3>What agents have reported</h3></div></div>{items.length?<ol className="history-list">{items.map((a)=><li key={a.id}><span><b>{a.workTitle}</b><small style={{whiteSpace:"pre-wrap"}}>{a.text}</small><time>{a.actor} · {new Date(a.at).toLocaleString()}</time></span></li>)}</ol>:<p className="empty-copy">No agent reports yet.</p>}</section></div>;
}

const PHASE_STATUS: Record<string,string> = { proposed: "Planned · not started", ready: "Approved · starting", running: "In progress", verifying: "Done · needs your review", completed: "Completed", failed: "Failed", cancelled: "Cancelled", waiting_human: "Waiting for your answer", blocked: "Blocked" };
function PhasesView({phases,items,busy,approve,decide,plan,review,alignmentCompleted,demo}:{phases:Phase[];items:Work[];busy:boolean;approve:(workId:string,revision:number,decision:"approve"|"reject"|"retry")=>Promise<boolean>;decide:(phaseId:string,moveOn:boolean)=>void;plan:()=>void;review:()=>void;alignmentCompleted:boolean;demo:boolean}) {
  const [confirm,setConfirm]=React.useState("");
  if (!phases.length) return <div className="phase-list"><section className="goal-card"><div className="goal-card-heading"><div><div className="eyebrow">PHASES</div><h3>No phases planned yet</h3></div><Layers3 size={17}/></div><p className="empty-copy">{demo?"The synthetic demo project does not use the VP.":alignmentCompleted?"The VP plans the whole project into phases on its own, each with exit criteria, then works each phase until the criteria are evidenced and stops for your review (or keeps going, in Full auto).":"Finish the alignment interview first; the VP plans the phases from your answers."}</p>{!demo&&alignmentCompleted&&<div className="form-actions"><button className="button primary" disabled={busy} onClick={plan}>Plan phases with the VP</button></div>}</section></div>;
  return <div className="phase-list">{phases.map((p)=>{
    const pct=p.tasks.total?Math.round(100*p.tasks.done/p.tasks.total):0;
    const unmet=p.assessment.filter((a)=>!a.met).length;
    const phaseItem=items.find((w)=>w.workId===p.phaseId);
    return <section key={p.phaseId} className={`goal-card phase-card ${p.status}`}><div className="goal-card-heading"><div><div className="eyebrow">PHASE {p.index} · {PHASE_STATUS[p.status]||p.status}</div><h3>{p.title}</h3></div></div>
      <p>{p.goal}</p>
      <div className="phase-progress" aria-label="Task progress"><span style={{width:`${pct}%`}}/></div>
      <small>{p.tasks.done} of {p.tasks.total} tasks done{p.tasks.running?` · ${p.tasks.running} running`:""}{p.tasks.failed?` · ${p.tasks.failed} failed`:""}{p.steers?` · ${p.steers} self-check${p.steers===1?"":"s"}`:""}</small>
      <div className="memory-section"><b>Exit criteria · how we know it&rsquo;s truly done</b><ul className="phase-criteria">{p.exitCriteria.map((c,i)=>{const a=p.assessment.find((x)=>x.criterion===c);return <li key={i} className={a?.met?"met":""}><span aria-hidden="true">{a?.met?"✓":"○"}</span> {c}{a?.met&&a.evidence&&<small>{a.evidence}</small>}</li>;})}</ul>{p.assessment.length>0&&p.status==="running"&&<small>{unmet} criteri{unmet===1?"on":"a"} still open; the VP is creating the work to close {unmet===1?"it":"them"}.</small>}</div>
      {items.some((w)=>w.parentWorkId===p.phaseId)&&<details className="phase-tasks"><summary>Tasks ({items.filter((w)=>w.parentWorkId===p.phaseId).length})</summary><ul>{items.filter((w)=>w.parentWorkId===p.phaseId).sort((a,b)=>a.title.localeCompare(b.title)).map((w)=><li key={w.workId}><span className={`thread-status-dot ${w.status}`}/><span>{w.title}</span><small>{w.status.replaceAll("_"," ")}</small></li>)}</ul></details>}
      {p.caveats.length>0&&<div className="memory-section"><b>Known weaknesses</b><ul>{p.caveats.map((c,i)=><li key={i}>{c}</li>)}</ul></div>}
      {(p.improvements.open>0||p.improvements.done>0)&&<div className="memory-section"><b>Improvement backlog · the VP works through this on its own</b><small>{p.improvements.open} open · {p.improvements.done} done</small>{p.improvements.titles.length>0&&<ul>{p.improvements.titles.map((t,i)=><li key={i}>{t}</li>)}</ul>}</div>}
      <div className="form-actions">
        {p.status==="proposed"&&p.index===1&&phaseItem&&<><button className="button primary" disabled={busy} onClick={()=>void approve(p.phaseId,phaseItem.revision,"approve")}>Accept the plan &amp; start</button><button className="button secondary" disabled={busy} onClick={review}>Change the plan…</button></>}
        {p.status==="proposed"&&p.index>1&&<small>Starts after you review the previous phase.</small>}
        {p.status==="verifying"&&<><button className="button primary" disabled={busy} onClick={review}>Review with the VP</button><button className="button secondary" disabled={busy} onClick={()=>decide(p.phaseId,false)}>Keep improving this phase</button><button className="button secondary" disabled={busy} onClick={()=>{if(confirm!==p.phaseId){setConfirm(p.phaseId);return;}setConfirm("");decide(p.phaseId,true);}}>{confirm===p.phaseId?"Really move on?":"Move on to the next phase"}</button></>}
        {p.status==="running"&&<button className="button secondary" disabled={busy} onClick={()=>{if(confirm!==p.phaseId){setConfirm(p.phaseId);return;}setConfirm("");decide(p.phaseId,true);}}>{confirm===p.phaseId?"Really close this phase?":"Close this phase now"}</button>}
      </div></section>;})}</div>;
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
  if (!sections.length && !pending.length && !logs.length) return <section className="goal-card"><div className="eyebrow">WHAT THE VP REMEMBERS</div><p className="empty-copy">Nothing yet. As you answer questions and talk to the VP, what matters is kept here, merged and corrected over time.</p></section>;
  return <section className="goal-card memory-card"><div className="goal-card-heading"><div><div className="eyebrow">WHAT THE VP REMEMBERS</div><h3>What every agent is told about you and the project</h3></div><button className="button secondary" onClick={onCorrect}>Something wrong? Tell the VP</button></div>
    {sections.map(([key,items])=><div key={key} className="memory-section"><b>{MEMORY_LABELS[key]||key}</b><ul>{items.map((item,index)=><li key={`${key}-${index}`}>{item}</li>)}</ul></div>)}
    {logs.length>0&&<details className="memory-section"><summary><b>Logged details ({logs.length}) · full detail agents are given</b></summary>{logs.map((log,index)=><div key={`l-${index}`} className="memory-log"><b>{log.crucial?"★ ":""}{log.title}</b> <small>{log.kind.replaceAll("_"," ")}</small><p>{log.detail}</p></div>)}</details>}
    {pending.length>0&&<div className="memory-section"><b>Just noted (not yet merged)</b><ul>{pending.map((item,index)=><li key={`p-${index}`}>{item}</li>)}</ul></div>}
    <p className="form-hint">This is curated from your conversations: duplicates merged, things you changed your mind about replaced, chit-chat dropped. Correct anything by telling the VP in chat.</p></section>;
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
