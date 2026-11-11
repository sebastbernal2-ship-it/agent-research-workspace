import React from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, ArrowDownRight, ArrowUpRight, Check, ChevronDown, CircleHelp,
  Command, FileText, FolderKanban, Inbox, Layers3, LayoutDashboard, Plus,
  Search, Settings2, ShieldAlert, Sparkles, SquareKanban, Timer, X,
} from "lucide-react";
import "./style.css";

type Project = {
  projectId: string; name: string; status: string;
  charter: { revision: number; goals: string[]; nonGoals: string[]; acceptanceCriteria: string[] };
};
type Work = { workId: string; title: string; objective: string; workType: string; status: string; dependsOn: string[]; revision: number };
type Summary = { project: Project; workItems: Work[]; recentEvents: { eventId: string; title: string; description: string; actorLabel: string; occurredAt: string }[]; counts: Record<string, number> };
type Question = { questionId: string; questionText: string; priority: string; blocking: boolean; workIds: string[]; etag: string };
type QuestionDetail = Question & { etag: string; record: { options: { optionId: string; label: string; consequence?: string }[]; impact: string; status: string; questionText: string }; affectedWork: { workId: string; title: string; status: string }[] };

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
  const [projectId, setProjectId] = React.useState("project.northstar");
  const [summary, setSummary] = React.useState<Summary | null>(null);
  const [view, setView] = React.useState<"overview" | "work" | "inbox">("overview");
  const [questions, setQuestions] = React.useState<Question[]>([]);
  const [selectedQuestion, setSelectedQuestion] = React.useState<QuestionDetail | null>(null);
  const [selectedOption, setSelectedOption] = React.useState("");
  const [answerNote, setAnswerNote] = React.useState("");
  const [decisionBusy, setDecisionBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [projectDialog, setProjectDialog] = React.useState(false);
  const [workDialog, setWorkDialog] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const loadProjects = React.useCallback(async () => {
    const result = await request<{items: Project[]}>('/api/v1/projects');
    setProjects(result.items);
    return result.items;
  }, []);

  const loadInbox = React.useCallback(async (id: string) => {
    const result = await request<{questions: Question[]}>(`/api/v1/projects/${encodeURIComponent(id)}/inbox?status=open`);
    setQuestions(result.questions);
    if (result.questions.length === 0) { setSelectedQuestion(null); return; }
    const detail = await request<QuestionDetail>(`/api/v1/projects/${encodeURIComponent(id)}/inbox/${encodeURIComponent(result.questions[0].questionId)}`);
    setSelectedQuestion(detail);
    setSelectedOption(detail.record.options[0]?.optionId || "");
    setAnswerNote("");
  }, []);

  const loadProject = React.useCallback(async (id = projectId) => {
    const result = await request<Summary>(`/api/v1/projects/${encodeURIComponent(id)}`);
    setProjectId(id);
    setSummary(result);
    setError("");
  }, [projectId]);

  React.useEffect(() => {
    void (async () => {
      try {
        await request('/api/v1/health');
        const items = await loadProjects();
        const preferred = items.find((project) => project.projectId === projectId) || items[0];
        if (preferred) { await loadProject(preferred.projectId); await loadInbox(preferred.projectId); }
      } catch (e) { setError(e instanceof Error ? e.message : "Local service unavailable."); }
    })();
  }, []); // initial service bootstrap

  const project = summary?.project;
  const workItems = summary?.workItems || [];
  const openWork = workItems.filter((item) => item.status === "proposed" || item.status === "ready").length;
  const runningWork = workItems.filter((item) => item.status === "running").length;

  async function createProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const created = await request<{project: Project}>('/api/v1/projects', { method: 'POST', body: JSON.stringify({ name: data.get('name'), goal: data.get('goal') }) });
      await loadProjects(); await loadProject(created.project.projectId); await loadInbox(created.project.projectId); setView("overview"); setProjectDialog(false); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create project."); }
    finally { setBusy(false); }
  }

  async function chooseQuestion(questionId: string) {
    if (!project) return;
    try {
      const detail = await request<QuestionDetail>(`/api/v1/projects/${encodeURIComponent(project.projectId)}/inbox/${encodeURIComponent(questionId)}`);
      setSelectedQuestion(detail); setSelectedOption(detail.record.options[0]?.optionId || ""); setAnswerNote("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load question."); }
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
        const text = [option?.label, answerNote.trim()].filter(Boolean).join("\\n\\nNote: ");
        if (!text) throw new Error("Choose an option or add a written response.");
        await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/inbox/${encodeURIComponent(selectedQuestion.questionId)}/answers`, { method: "POST", body: JSON.stringify({ expectedEtag: selectedQuestion.etag, text, ...(option ? { selectedOptionId: option.optionId } : {}) }) });
      }
      await loadProject(project.projectId); await loadInbox(project.projectId);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save the decision."); }
    finally { setDecisionBusy(false); }
  }

  async function createWork(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!project) return; setBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/work`, { method: 'POST', body: JSON.stringify({ title: data.get('title'), objective: data.get('objective'), workType: data.get('workType') }) });
      await loadProject(project.projectId); setView("work"); setWorkDialog(false); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not add work."); }
    finally { setBusy(false); }
  }

  return <div className="shell">
    <aside className="sidebar">
      <a className="brand" href="#home"><span className="brand-icon"><Layers3 size={18}/></span><span>Northstar</span></a>
      <div className="workspace-switch"><span className="workspace-mark">V</span><span><b>Personal workspace</b><small>{projects.length} {projects.length === 1 ? "project" : "projects"}</small></span><ChevronDown size={15}/></div>
      <div className="section-caption">WORKSPACE</div>
      <nav className="main-nav" aria-label="Workspace navigation">
        <button className="nav-link active" type="button"><LayoutDashboard size={16}/>Overview</button>
        <button className="nav-link" type="button" onClick={() => setView("work")}><SquareKanban size={16}/>Projects</button>
        <button className={`nav-link ${view === "inbox" ? "active" : ""}`} type="button" onClick={() => { setView("inbox"); if (project) void loadInbox(project.projectId); }}><Inbox size={16}/>Inbox <span className="nav-badge">{summary?.counts.openQuestions || 0}</span></button>
        <button className="nav-link disabled" type="button" title="No agents are connected"><Sparkles size={16}/>Agents</button>
      </nav>
      <div className="project-label"><span className="section-caption">YOUR PROJECTS</span><button type="button" aria-label="Create project" onClick={() => setProjectDialog(true)}><Plus size={15}/></button></div>
      <div className="project-list">{projects.map((item) => <button key={item.projectId} className={`project-link ${item.projectId === projectId ? "selected" : ""}`} onClick={() => { void loadProject(item.projectId); void loadInbox(item.projectId); }}><span className="project-glyph"><FolderKanban size={15}/></span><span>{item.name}</span></button>)}</div>
      <div className="sidebar-bottom"><button className="nav-link disabled"><Settings2 size={16}/>Settings</button><div className="user-row"><div className="user-avatar">V</div><span><b>Local user</b><small>Prototype · not authenticated</small></span><CircleHelp size={16}/></div></div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="breadcrumbs"><span>Workspace</span><span className="slash">/</span><b>{project?.name || "Projects"}</b></div><div className="top-actions"><span className="status-pill"><i/>Local prototype</span><button className="icon-button" aria-label="Search is not connected" title="Search is not connected"><Search size={17}/></button><button className="icon-button" aria-label="Commands are not connected" title="Commands are not connected"><Command size={17}/></button></div></header>
      <div className="content">
        <div className="prototype-notice"><ShieldAlert size={15}/><span>Prototype · test data only · no model, browser, or agent execution connected</span></div>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {project && <>
          <div className="page-heading"><div><div className="eyebrow">PROJECT WORKSPACE <span className="dot"/> {project.status.toUpperCase()}</div><h1>{project.name}</h1><p className="subhead">{project.charter.goals.join(" ")}</p></div><div className="heading-buttons"><button className="button secondary" onClick={() => setWorkDialog(true)}><Plus size={15}/>Add work</button><button className="button primary" disabled title="Agent execution is not connected"><Sparkles size={15}/>Start work</button></div></div>
          <div className="view-tabs"><button className={view === "overview" ? "tab active" : "tab"} onClick={() => setView("overview")}>Overview</button><button className={view === "work" ? "tab active" : "tab"} onClick={() => setView("work")}>Work <span>{workItems.length}</span></button><button className="tab unavailable" title="Evidence view is not yet implemented">Evidence</button></div>
          <div className="stats-grid">
            <Stat icon={<SquareKanban size={16}/>} label="WORK ITEMS" value={String(workItems.length)} note={`${openWork} proposed or ready`} trend="neutral"/>
            <Stat icon={<Activity size={16}/>} label="IN PROGRESS" value={String(runningWork)} note="No workers connected" trend="neutral"/>
            <Stat icon={<Inbox size={16}/>} label="NEEDS YOUR INPUT" value={String(summary.counts.openQuestions || 0)} note="Questions in project inbox" trend="neutral"/>
            <Stat icon={<FileText size={16}/>} label="PROJECT HISTORY" value={String(summary.counts.eventSequence || 0)} note="Canonical events recorded" trend="up"/>
          </div>
          {view === "overview" ? <div className="dashboard-grid">
            <section className="panel plan-panel"><div className="panel-header"><div><div className="eyebrow">PROJECT PLAN</div><h2>Work items</h2></div><button className="link-button" onClick={() => setView("work")}>View all <ArrowUpRight size={14}/></button></div>
              {workItems.length ? <WorkTable items={workItems.slice(0, 6)}/> : <Empty title="No work yet" text="Add proposed work to start shaping this project."/>}
            </section>
            <section className="panel activity-panel"><div className="panel-header"><div><div className="eyebrow">CANONICAL HISTORY</div><h2>Recent activity</h2></div><Activity size={16} className="muted-icon"/></div>
              {summary.recentEvents.length ? <ol className="activity-list">{summary.recentEvents.slice(0, 6).map((event) => <li key={event.eventId}><span className="activity-mark"><span/></span><div><b>{event.title}</b><p>{event.description}</p><small>{event.actorLabel} · {new Date(event.occurredAt).toLocaleString()}</small></div></li>)}</ol> : <Empty title="No recent activity" text="Project events will appear here."/>}
            </section>
            <section className="panel charter-panel"><div className="panel-header"><div><div className="eyebrow">PROJECT CHARTER · REVISION {project.charter.revision}</div><h2>Purpose</h2></div><FileText size={16} className="muted-icon"/></div><p>{project.charter.goals.join(" ")}</p><div className="charter-meta"><span><Check size={14}/>{project.charter.acceptanceCriteria.length} acceptance criteria</span><span><Timer size={14}/>Project-local</span></div></section>
            <section className="panel next-panel"><div className="next-icon"><ArrowDownRight size={17}/></div><div><div className="eyebrow">NEXT STEP</div><h2>Build the project plan</h2><p>Add proposed work items; agent execution will be added in a later demo slice.</p><button className="link-button" onClick={() => setWorkDialog(true)}>Add the first item <ArrowUpRight size={14}/></button></div></section>
          </div> : view === "work" ? <section className="panel work-page"><div className="panel-header"><div><div className="eyebrow">PROJECT PLAN</div><h2>Work items</h2></div><button className="button secondary" onClick={() => setWorkDialog(true)}><Plus size={15}/>Add work</button></div>{workItems.length ? <WorkTable items={workItems}/> : <Empty title="No work items" text="Add a proposed work item to this project."/>}</section> : <InboxView questions={questions} selected={selectedQuestion} selectedOption={selectedOption} setSelectedOption={setSelectedOption} note={answerNote} setNote={setAnswerNote} busy={decisionBusy} choose={chooseQuestion} answer={() => void submitDecision(false)} reject={() => void submitDecision(true)}/>}
        </>}
        {!project && !error && <div className="loading">Loading your projects…</div>}
        <footer className="footer">Canonical project history · WSL local prototype <span>No agents are running</span></footer>
      </div>
    </main>
    {projectDialog && <Modal title="Create a project" onClose={() => setProjectDialog(false)}><form onSubmit={createProject}><label>Project name<input name="name" required maxLength={100} autoFocus placeholder="e.g. Reading list"/></label><label>Project goal<textarea name="goal" required maxLength={1000} rows={3} placeholder="What are you trying to accomplish?"/></label><div className="form-actions"><button type="button" className="button secondary" onClick={() => setProjectDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Creating…" : "Create project"}</button></div></form></Modal>}
    {workDialog && <Modal title="Add proposed work" onClose={() => setWorkDialog(false)}><form onSubmit={createWork}><label>Title<input name="title" required maxLength={160} autoFocus placeholder="What needs to be done?"/></label><label>Objective<textarea name="objective" required maxLength={2000} rows={3} placeholder="Describe the intended outcome."/></label><label>Type<select name="workType"><option value="research">Research</option><option value="planning">Planning</option><option value="implementation">Implementation</option><option value="review">Review</option></select></label><p className="form-hint">This creates a proposal in canonical history. It does not start an agent.</p><div className="form-actions"><button type="button" className="button secondary" onClick={() => setWorkDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Saving…" : "Add proposed work"}</button></div></form></Modal>}
  </div>;
}

function InboxView({questions,selected,selectedOption,setSelectedOption,note,setNote,busy,choose,answer,reject}:{questions:Question[];selected:QuestionDetail|null;selectedOption:string;setSelectedOption:(value:string)=>void;note:string;setNote:(value:string)=>void;busy:boolean;choose:(id:string)=>void;answer:()=>void;reject:()=>void}) {
  return <div className="inbox-layout"><section className="panel inbox-questions"><div className="panel-header"><div><div className="eyebrow">PROJECT DECISIONS</div><h2>Open questions</h2></div><span className="type-tag">{questions.length}</span></div>{questions.length ? questions.map((question)=><button key={question.questionId} className={`question-link ${selected?.questionId===question.questionId?"chosen":""}`} onClick={()=>choose(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking?"Blocks "+question.workIds.length+" work item(s)":"Non-blocking"}</small></span></button>) : <Empty title="Inbox clear" text="No open project questions."/>}</section><section className="panel inbox-detail">{selected ? <><div className="eyebrow">{selected.blocking?"BLOCKING DECISION":"PROJECT QUESTION"}</div><h2>{selected.questionText}</h2><p className="question-impact">{selected.record.impact}</p><div className="affected-list"><b>AFFECTED WORK</b>{selected.affectedWork.map((item)=><span key={item.workId}>{item.title}<small>{item.status.replaceAll("_"," ")}</small></span>)}</div>{selected.record.options.length>0&&<fieldset className="decision-options"><legend>Choose an option</legend>{selected.record.options.map((option)=><label key={option.optionId}><input type="radio" name="decision" value={option.optionId} checked={selectedOption===option.optionId} onChange={()=>setSelectedOption(option.optionId)}/><span><b>{option.label}</b>{option.consequence&&<small>{option.consequence}</small>}</span></label>)}</fieldset>}<label className="note-label">{selected.record.options.length?"NOTE (OPTIONAL)":"YOUR RESPONSE"}<textarea value={note} onChange={(event)=>setNote(event.target.value)} rows={3} placeholder="Add context or a written response…"/></label><div className="decision-actions"><button className="button secondary" disabled={busy} onClick={reject}>Reject</button><button className="button primary" disabled={busy} onClick={answer}>{busy?"Saving…":"Record decision"}</button></div><p className="form-hint">Your decision is checked against the current question and affected-work revision.</p></> : <Empty title="Select a question" text="Choose an open item to review its impact and record a decision."/>}</section></div>;
}

function Stat({icon,label,value,note,trend}:{icon:React.ReactNode;label:string;value:string;note:string;trend:"up"|"neutral"}) { return <section className="stat-card"><div className="stat-head"><span>{label}</span><span className="stat-icon">{icon}</span></div><div className="stat-value">{value}</div><div className="stat-note">{trend === "up" ? <ArrowUpRight size={13}/> : null}{note}</div></section>; }
function WorkTable({items}:{items:Work[]}) { return <div className="table-wrap"><table><thead><tr><th>WORK ITEM</th><th>TYPE</th><th>STATUS</th><th>REV.</th></tr></thead><tbody>{items.map((item) => <tr key={item.workId}><td><b>{item.title}</b><small>{item.objective}</small></td><td><span className="type-tag">{item.workType}</span></td><td><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_", " ")}</span></td><td className="revision">{item.revision}</td></tr>)}</tbody></table></div>; }
function Empty({title,text}:{title:string;text:string}) { return <div className="empty"><div className="empty-icon"><FolderKanban size={18}/></div><b>{title}</b><p>{text}</p></div>; }
function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:React.ReactNode}) { return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17}/></button></div>{children}</section></div>; }

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
