import React from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, ChevronDown, Files, Folder, FolderKanban, GitBranch, Inbox,
  Layers3, MessageSquare, MoreHorizontal, Paperclip, Plus, Search, Send,
  ShieldAlert, Sparkles, Terminal, X,
} from "lucide-react";
import "./style.css";

type Project = { projectId: string; name: string; status: string; charter: { revision: number; goals: string[]; nonGoals: string[]; acceptanceCriteria: string[] } };
type Work = { workId: string; title: string; objective: string; workType: string; status: string; dependsOn: string[]; revision: number };
type ProjectEvent = { eventId: string; title: string; description: string; actorLabel: string; occurredAt: string };
type Summary = { project: Project; workItems: Work[]; recentEvents: ProjectEvent[]; counts: Record<string, number> };
type Question = { questionId: string; questionText: string; priority: string; blocking: boolean; workIds: string[]; etag: string };
type QuestionDetail = Question & { record: { options: { optionId: string; label: string; consequence?: string }[]; impact: string; status: string; questionText: string }; affectedWork: { workId: string; title: string; status: string }[] };
type View = "thread" | "inbox";
type InspectorTab = "Changes" | "Files" | "Terminal";

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
  const [view, setView] = React.useState<View>("thread");
  const [inspector, setInspector] = React.useState<InspectorTab>("Changes");
  const [projectsOpen, setProjectsOpen] = React.useState(false);
  const [selectedWorkId, setSelectedWorkId] = React.useState("");
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
    if (!result.questions.length) { setSelectedQuestion(null); return; }
    const detail = await request<QuestionDetail>(`/api/v1/projects/${encodeURIComponent(id)}/inbox/${encodeURIComponent(result.questions[0].questionId)}`);
    setSelectedQuestion(detail);
    setSelectedOption(detail.record.options[0]?.optionId || "");
    setAnswerNote("");
  }, []);

  const loadProject = React.useCallback(async (id = projectId) => {
    const result = await request<Summary>(`/api/v1/projects/${encodeURIComponent(id)}`);
    setProjectId(id);
    setSummary(result);
    setSelectedWorkId((current) => result.workItems.some((item) => item.workId === current) ? current : result.workItems[0]?.workId || "");
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
  const selectedWork = workItems.find((item) => item.workId === selectedWorkId) || null;
  const openQuestionCount = summary?.counts.openQuestions || 0;

  async function createProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const form = event.currentTarget; const data = new FormData(form);
    try {
      const result = await request<{project: Project}>('/api/v1/projects', { method: 'POST', body: JSON.stringify({ name: data.get('name'), goal: data.get('goal') }) });
      await loadProjects(); await loadProject(result.project.projectId); await loadInbox(result.project.projectId);
      setView("thread"); setProjectDialog(false); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create project."); }
    finally { setBusy(false); }
  }

  async function createWork(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!project) return; setBusy(true);
    const form = event.currentTarget; const data = new FormData(form);
    try {
      const result = await request<{workItem: Work}>(`/api/v1/projects/${encodeURIComponent(project.projectId)}/work`, { method: 'POST', body: JSON.stringify({ title: data.get('title'), objective: data.get('objective'), workType: data.get('workType') }) });
      setSelectedWorkId(result.workItem.workId); await loadProject(project.projectId); setView("thread"); setWorkDialog(false); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not add work."); }
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
        const text = [option?.label, answerNote.trim()].filter(Boolean).join("\n\nNote: ");
        if (!text) throw new Error("Choose an option or add a written response.");
        await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/inbox/${encodeURIComponent(selectedQuestion.questionId)}/answers`, { method: "POST", body: JSON.stringify({ expectedEtag: selectedQuestion.etag, text, ...(option ? { selectedOptionId: option.optionId } : {}) }) });
      }
      await loadProject(project.projectId); await loadInbox(project.projectId);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save the decision."); }
    finally { setDecisionBusy(false); }
  }

  async function switchProject(id: string) {
    try { await loadProject(id); await loadInbox(id); setView("thread"); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the project."); }
  }

  return <div className="amp-shell">
    <aside className="amp-sidebar">
      <div className="amp-brand-row"><button className="panel-toggle" aria-label="Sidebar collapse unavailable" title="Sidebar collapse is not implemented" disabled><span/></button><a className="amp-brand" href="#workspace"><span className="amp-logo"><Layers3 size={16}/></span><b>Northstar</b></a><button className="icon-button" aria-label="Search unavailable" title="Search is not connected" disabled><Search size={15}/></button><button className="new-thread" aria-label="New project" title="New project" onClick={() => setProjectDialog(true)}><Plus size={16}/></button></div>
      <button className="amp-project-picker" onClick={() => setProjectsOpen((open) => !open)}><Folder size={15}/><span>{project?.name || "Projects"}</span><ChevronDown size={13}/></button>
      {projectsOpen && <div className="project-menu">{projects.map((item)=><button key={item.projectId} className={item.projectId===projectId?"current":""} onClick={()=>{void switchProject(item.projectId);setProjectsOpen(false);}}>{item.name}</button>)}<button onClick={()=>{setProjectDialog(true);setProjectsOpen(false);}}><Plus size={13}/>New project</button></div>}
      <div className="sidebar-section-head"><span>WORK ITEMS</span><button onClick={() => setWorkDialog(true)} aria-label="Add work item"><Plus size={14}/></button></div>
      <div className="thread-list">{workItems.map((item) => <button key={item.workId} className={`thread-row ${selectedWorkId === item.workId && view === "thread" ? "thread-selected" : ""}`} onClick={() => { setSelectedWorkId(item.workId); setView("thread"); }}><span className={`thread-status-dot ${item.status}`}/><span className="thread-title">{item.title}</span><span className="thread-menu"><MoreHorizontal size={14}/></span></button>)}</div>
      <div className="sidebar-spacer"/>
      <div className="amp-sidebar-links"><button onClick={() => setView("inbox")} className={view === "inbox" ? "current" : ""}><Inbox size={15}/>Inbox <span>{openQuestionCount || ""}</span></button><button disabled title="Activity view is not connected"><Activity size={15}/>Activity</button><button onClick={() => setProjectDialog(true)}><FolderKanban size={15}/>Projects</button></div>
      <div className="amp-user"><span className="amp-avatar">V</span><span><b>Local user</b><small>Prototype · not authenticated</small></span><MoreHorizontal size={15}/></div>
    </aside>
    <main className="amp-main">
      <header className="thread-header"><div className="thread-heading"><span className="project-dot"/><div><h1>{view === "inbox" ? "Project inbox" : selectedWork?.title || project?.name || "Workspace"}</h1><small>{project?.name || "Local project"}{selectedWork ? ` · ${selectedWork.workType} · ${selectedWork.status.replaceAll("_", " ")}` : " · canonical project history"}</small></div><button className="icon-button" title="Thread actions are unavailable" aria-label="Thread actions unavailable" disabled><MoreHorizontal size={17}/></button></div><div className="thread-actions"><button className="icon-button" title="Sharing is unavailable" aria-label="Share unavailable" disabled><MessageSquare size={15}/></button><span className="branch-pill"><GitBranch size={13}/>{project?.projectId || "local"}</span><span className="thread-size">Local</span></div></header>
      <div className="amp-workspace">
        <section className="conversation-pane">
          {error && <div className="error-banner" role="alert">{error}</div>}
          {view === "inbox" ? <InboxView questions={questions} selected={selectedQuestion} selectedOption={selectedOption} setSelectedOption={setSelectedOption} note={answerNote} setNote={setAnswerNote} busy={decisionBusy} choose={chooseQuestion} answer={() => void submitDecision(false)} reject={() => void submitDecision(true)}/> : <>
            <div className="thread-scroll">
              <div className="thread-intro"><div className="intro-icon"><Sparkles size={16}/></div><div><span className="eyebrow">PROJECT THREAD</span><h2>{selectedWork?.title || project?.name || "Welcome"}</h2><p>{selectedWork?.objective || project?.charter.goals.join(" ") || "Create a project to get started."}</p></div></div>
              {selectedWork && <div className="user-turn"><div className="user-turn-label"><span className="amp-avatar small-avatar">V</span><b>Project owner</b><time>Work request</time></div><p>{selectedWork.objective}</p></div>}
              <div className="agent-placeholder"><div className="placeholder-line"/><div className="agent-placeholder-body"><Sparkles size={15}/><div><b>No agent response yet</b><p>This project has durable work history, but no model or worker is connected. Starting an agent is intentionally unavailable in this prototype.</p></div></div></div>
              {summary?.recentEvents?.length ? <div className="thread-events"><div className="events-heading"><span>PROJECT ACTIVITY</span><span>{summary.recentEvents.length} events</span></div>{summary.recentEvents.slice(0,5).map((event)=><div className="event-row" key={event.eventId}><span className="event-bullet"/><span><b>{event.title}</b><small>{event.description}</small></span><time>{new Date(event.occurredAt).toLocaleDateString()}</time></div>)}</div> : null}
            </div>
            <div className="composer-wrap"><div className="composer-disabled"><textarea aria-label="Message agent" disabled placeholder="Connect an agent to start this thread…"/><div className="composer-tools"><button disabled aria-label="Attach file"><Paperclip size={16}/></button><span>Agent execution is not connected</span><button disabled aria-label="Send message"><Send size={15}/></button></div></div><div className="composer-caption">Project context stays in canonical history · no model calls are made</div></div>
          </>}
        </section>
        <aside className="review-pane"><div className="review-tabs" role="tablist" aria-label="Workspace tools">{([['Changes',GitBranch],['Files',Files],['Terminal',Terminal]] as const).map(([tab,Icon])=><button role="tab" aria-selected={inspector===tab} className={inspector===tab?"review-tab active":"review-tab"} key={tab} onClick={()=>setInspector(tab)}><Icon size={14}/>{tab}</button>)}</div><div className="review-toolbar"><span>{inspector === "Changes" ? "PROJECT CHANGES" : inspector.toUpperCase()}</span><button className="icon-button" title="Panel options are unavailable" aria-label="Panel options unavailable" disabled><MoreHorizontal size={15}/></button></div>{inspector === "Changes" ? <><div className="change-summary"><span className="change-count">0</span><span><b>No file changes</b><small>Nothing has been edited in this project.</small></span></div><div className="review-divider"/><div className="review-section-title">PROJECT WORK <span>{workItems.length}</span></div><div className="review-work-list">{workItems.map((item)=><button key={item.workId} className={item.workId===selectedWorkId?"review-work selected":"review-work"} onClick={()=>{setSelectedWorkId(item.workId);setView("thread");}}><span className={`thread-status-dot ${item.status}`}/><span><b>{item.title}</b><small>{item.status.replaceAll("_"," ")} · rev {item.revision}</small></span></button>)}</div></> : inspector === "Files" ? <div className="inspector-empty"><Files size={18}/><b>Project files</b><p>File browsing is not connected. The workspace folder is not exposed to this UI.</p></div> : <div className="inspector-empty"><Terminal size={18}/><b>Terminal unavailable</b><p>No worker or project shell is running.</p></div>}
          <div className="review-footer"><ShieldAlert size={14}/><span>Local prototype · test data only</span></div>
        </aside>
      </div>
    </main>
    {projectDialog && <Modal title="Create a project" onClose={() => setProjectDialog(false)}><form onSubmit={createProject}><label>Project name<input name="name" required maxLength={100} autoFocus placeholder="e.g. Reading list"/></label><label>Project goal<textarea name="goal" required maxLength={1000} rows={3} placeholder="What are you trying to accomplish?"/></label><div className="form-actions"><button type="button" className="button secondary" onClick={() => setProjectDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Creating…" : "Create project"}</button></div></form></Modal>}
    {workDialog && <Modal title="Add a work item" onClose={() => setWorkDialog(false)}><form onSubmit={createWork}><label>Title<input name="title" required maxLength={160} autoFocus placeholder="What needs to be done?"/></label><label>Objective<textarea name="objective" required maxLength={2000} rows={3} placeholder="Describe the intended outcome."/></label><label>Type<select name="workType"><option value="research">Research</option><option value="planning">Planning</option><option value="implementation">Implementation</option><option value="review">Review</option></select></label><p className="form-hint">Work is recorded in canonical history; no agent will execute it yet.</p><div className="form-actions"><button type="button" className="button secondary" onClick={() => setWorkDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Saving…" : "Add work"}</button></div></form></Modal>}
  </div>;
}

function InboxView({questions,selected,selectedOption,setSelectedOption,note,setNote,busy,choose,answer,reject}:{questions:Question[];selected:QuestionDetail|null;selectedOption:string;setSelectedOption:(value:string)=>void;note:string;setNote:(value:string)=>void;busy:boolean;choose:(id:string)=>void;answer:()=>void;reject:()=>void}) {
  return <div className="inbox-layout"><section className="inbox-questions"><div className="inbox-heading"><span>OPEN QUESTIONS</span><span>{questions.length}</span></div>{questions.length ? questions.map((question)=><button key={question.questionId} className={`question-link ${selected?.questionId===question.questionId?"chosen":""}`} onClick={()=>choose(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking?"Blocks "+question.workIds.length+" work item(s)":"Non-blocking"}</small></span></button>) : <div className="inbox-empty"><Inbox size={19}/><b>Inbox clear</b><small>No open project questions.</small></div>}</section><section className="inbox-detail">{selected ? <><div className="eyebrow">{selected.blocking?"BLOCKING DECISION":"PROJECT QUESTION"}</div><h2>{selected.questionText}</h2><p>{selected.record.impact}</p><div className="affected-list"><b>AFFECTED WORK</b>{selected.affectedWork.map((item)=><span key={item.workId}>{item.title}<small>{item.status.replaceAll("_"," ")}</small></span>)}</div>{selected.record.options.length>0&&<fieldset className="decision-options"><legend>Choose an option</legend>{selected.record.options.map((option)=><label key={option.optionId}><input type="radio" name="decision" value={option.optionId} checked={selectedOption===option.optionId} onChange={()=>setSelectedOption(option.optionId)}/><span><b>{option.label}</b>{option.consequence&&<small>{option.consequence}</small>}</span></label>)}</fieldset>}<label className="note-label">{selected.record.options.length?"NOTE (OPTIONAL)":"YOUR RESPONSE"}<textarea value={note} onChange={(event)=>setNote(event.target.value)} rows={3} placeholder="Add context or a written response…"/></label><div className="decision-actions"><button className="button secondary" disabled={busy} onClick={reject}>Reject</button><button className="button primary" disabled={busy} onClick={answer}>{busy?"Saving…":"Record decision"}</button></div><p className="form-hint">Decision is checked against the current question and work revision.</p></> : <div className="inbox-empty"><Inbox size={19}/><b>Select a question</b><small>Choose an open item to review and decide.</small></div>}</section></div>;
}

function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:React.ReactNode}) { return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17}/></button></div>{children}</section></div>; }

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
