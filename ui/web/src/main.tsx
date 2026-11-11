import React from "react";
import { createRoot } from "react-dom/client";
import {
  Check, ChevronDown, CircleHelp, Folder, FolderKanban, Inbox,
  Layers3, Plus, Search, ShieldAlert, Sparkles, X,
} from "lucide-react";
import "./style.css";
import "./goal.css";

type Project = { projectId: string; name: string; status: string; charter: { revision: number; goals: string[]; nonGoals: string[]; acceptanceCriteria: string[] } };
type Work = { workId: string; title: string; objective: string; workType: string; status: string; dependsOn: string[]; revision: number };
type ProjectEvent = { eventId: string; title: string; description: string; actorLabel: string; occurredAt: string };
type Summary = { project: Project; workItems: Work[]; recentEvents: ProjectEvent[]; counts: Record<string, number> };
type Question = { questionId: string; questionText: string; priority: string; blocking: boolean; workIds: string[]; etag: string };
type QuestionDetail = Question & { record: { options: { optionId: string; label: string; consequence?: string }[]; impact: string; status: string; questionText: string }; affectedWork: { workId: string; title: string; status: string }[] };
type View = "overview" | "inbox";

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
  const pulse = projectPulse(workItems.length, openQuestionCount, blockingQuestionCount);

  async function createProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const form = event.currentTarget; const data = new FormData(form);
    try {
      const result = await request<{project: Project}>('/api/v1/projects', { method: 'POST', body: JSON.stringify({ name: data.get('name'), goal: data.get('goal') }) });
      await loadProjects(); await loadProject(result.project.projectId); await loadInbox(result.project.projectId);
      setView("overview"); setProjectDialog(false); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create project."); }
    finally { setBusy(false); }
  }

  async function createWork(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!project) return; setBusy(true);
    const form = event.currentTarget; const data = new FormData(form);
    try {
      await request(`/api/v1/projects/${encodeURIComponent(project.projectId)}/work`, { method: 'POST', body: JSON.stringify({ title: data.get('title'), objective: data.get('objective'), workType: data.get('workType') }) });
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
      await loadProject(project.projectId); await loadInbox(project.projectId);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save the decision."); }
    finally { setDecisionBusy(false); }
  }

  async function switchProject(id: string) {
    try { await loadProject(id); await loadInbox(id); setView("overview"); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the project."); }
  }

  return <div className="amp-shell">
    <aside className="amp-sidebar">
      <div className="amp-brand-row"><button className="panel-toggle" aria-label="Sidebar collapse unavailable" title="Sidebar collapse is not implemented" disabled><span/></button><a className="amp-brand" href="#workspace"><span className="amp-logo"><Layers3 size={16}/></span><b>Northstar</b></a><button className="icon-button" aria-label="Search unavailable" title="Search is not connected" disabled><Search size={15}/></button><button className="new-thread" aria-label="New project" title="New project" onClick={() => setProjectDialog(true)}><Plus size={16}/></button></div>
      <button className="amp-project-picker" onClick={() => setProjectsOpen((open) => !open)}><Folder size={15}/><span>{project?.name || "Projects"}</span><ChevronDown size={13}/></button>
      {projectsOpen && <div className="project-menu">{projects.map((item)=><button key={item.projectId} className={item.projectId===projectId?"current":""} onClick={()=>{void switchProject(item.projectId);setProjectsOpen(false);}}>{item.name}</button>)}<button onClick={()=>{setProjectDialog(true);setProjectsOpen(false);}}><Plus size={13}/>New project</button></div>}
      <div className="sidebar-section-head"><span>PROJECT</span></div>
      <nav className="amp-sidebar-links" aria-label="Project navigation"><button onClick={() => setView("overview")} className={view === "overview" ? "current" : ""}><Layers3 size={15}/>Overview</button><button onClick={() => setView("inbox")} className={view === "inbox" ? "current" : ""}><Inbox size={15}/>Your input <span>{openQuestionCount || ""}</span></button></nav>
      <div className="sidebar-spacer"/>
      <div className="amp-sidebar-links"><button onClick={() => setProjectDialog(true)}><FolderKanban size={15}/>New project</button></div>
      <div className="amp-user"><span className="amp-avatar">V</span><span><b>Local user</b><small>Prototype · not authenticated</small></span><CircleHelp size={15}/></div>
    </aside>
    <main className="goal-main">
      <header className="goal-topbar"><div className="goal-topbar-title"><span className="project-dot"/><div><h1>{view === "inbox" ? "Your input" : project?.name || "Project"}</h1><small>{view === "inbox" ? "Decisions that need your attention" : "Goal, outcomes, and what needs attention"}</small></div></div><div className="goal-topbar-actions"><span className="prototype-status"><ShieldAlert size={13}/>Prototype · test data only</span>{view === "overview" && <button className="button primary" onClick={() => setWorkDialog(true)}><Plus size={14}/>Add to plan</button>}</div></header>
      <div className="goal-stage">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {view === "inbox" ? <InboxView questions={questions} selected={selectedQuestion} selectedOption={selectedOption} setSelectedOption={setSelectedOption} note={answerNote} setNote={setAnswerNote} busy={decisionBusy} choose={chooseQuestion} answer={() => void submitDecision(false)} reject={() => void submitDecision(true)}/> : project ? <div className="goal-scroll">
          <section className="goal-hero"><div className="eyebrow"><Sparkles size={13}/> WHAT YOU WANT TO ACHIEVE</div><h2>{project.charter.goals.join(" ") || "No project goal defined"}</h2><div className="goal-project-meta"><span>{project.name}</span><span>Charter revision {project.charter.revision}</span></div></section>
          <section className={`project-pulse ${openQuestionCount ? "needs-input" : ""}`} aria-labelledby="pulse-title"><div className="pulse-icon"><Sparkles size={17}/></div><div className="pulse-copy"><div className="eyebrow">PROJECT UPDATE</div><h3 id="pulse-title">{pulse.title}</h3><p>{pulse.description}</p></div>{openQuestionCount > 0 && <button className="button secondary" onClick={() => setView("inbox")}>Review {openQuestionCount === 1 ? "decision" : `${openQuestionCount} decisions`} <span aria-hidden="true">→</span></button>}</section>
          <div className="goal-columns">
            <section className="goal-card criteria-card"><div className="goal-card-heading"><div><div className="eyebrow">THE FINISH LINE</div><h3>How you’ll know it worked</h3></div><Check size={17}/></div>{project.charter.acceptanceCriteria.length ? <><ul className="criteria-list">{project.charter.acceptanceCriteria.map((criterion,index)=><li key={`${index}-${criterion}`}><span className="criteria-marker"/>{criterion}</li>)}</ul><p className="subtle-note">These are the project’s stated criteria; this prototype does not verify outcomes against them.</p></> : <p className="empty-copy">No success criteria are recorded in this project charter yet.</p>}</section>
            <section className="goal-card attention-card"><div className="goal-card-heading"><div><div className="eyebrow">YOUR INPUT</div><h3>{openQuestionCount ? `${openQuestionCount} ${openQuestionCount === 1 ? "decision" : "decisions"} to review` : "Nothing needs your attention"}</h3></div><Inbox size={17}/></div>{questions.length ? <div className="attention-list">{questions.slice(0,3).map((question)=><button key={question.questionId} className="attention-item" onClick={()=>void openQuestion(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking ? "Your answer is needed before affected work can continue" : "Your answer may shape the project direction"}</small></span><span aria-hidden="true">→</span></button>)}</div> : <p className="empty-copy">Open project decisions will appear here. You won’t need to follow the agents’ internal discussion.</p>}</section>
          </div>
          <details className="detail-disclosure"><summary><span><b>Work plan details</b><small>Implementation-level items are here when you want them</small></span><span className="detail-count">{workItems.length}</span></summary>{workItems.length ? <div className="plan-list">{workItems.map((item)=><article className="plan-item" key={item.workId}><div className="plan-item-title"><span className={`thread-status-dot ${item.status}`}/><b>{item.title}</b><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div><p>{item.objective}</p><small>{item.workType} · revision {item.revision}</small></article>)}</div> : <p className="empty-copy">No work has been planned yet.</p>}</details>
          <details className="detail-disclosure history-disclosure"><summary><span><b>Project history</b><small>Event-by-event detail, kept out of the main progress view</small></span><span className="detail-count">{summary?.recentEvents.length || 0}</span></summary>{summary?.recentEvents.length ? <ol className="history-list">{summary.recentEvents.map((event)=><li key={event.eventId}><span><b>{event.title}</b><small>{event.description}</small><time>{event.actorLabel} · {new Date(event.occurredAt).toLocaleString()}</time></span></li>)}</ol> : <p className="empty-copy">No project events have been recorded yet.</p>}</details>
          <div className="goal-footnote"><ShieldAlert size={13}/>No model or worker is connected. This demo cannot report agent progress or verified outcomes.</div>
        </div> : <div className="loading">Loading your project…</div>}
      </div>
    </main>
    {projectDialog && <Modal title="Create a project" onClose={() => setProjectDialog(false)}><form onSubmit={createProject}><label>Project name<input name="name" required maxLength={100} autoFocus placeholder="e.g. Reading list"/></label><label>What do you want to accomplish?<textarea name="goal" required maxLength={1000} rows={3} placeholder="What do you want to be true when this succeeds?"/></label><div className="form-actions"><button type="button" className="button secondary" onClick={() => setProjectDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Creating…" : "Create project"}</button></div></form></Modal>}
    {workDialog && <Modal title="Add to the project plan" onClose={() => setWorkDialog(false)}><form onSubmit={createWork}><label>Outcome or milestone<input name="title" required maxLength={160} autoFocus placeholder="What meaningful result should this produce?"/></label><label>What will be different when this is done?<textarea name="objective" required maxLength={2000} rows={3} placeholder="Describe the result and how it contributes to the project goal."/></label><label>Category<select name="workType"><option value="research">Research</option><option value="planning">Planning</option><option value="implementation">Implementation</option><option value="review">Review</option></select></label><p className="form-hint">This records a proposal in project history; it does not start an agent.</p><div className="form-actions"><button type="button" className="button secondary" onClick={() => setWorkDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Saving…" : "Add to plan"}</button></div></form></Modal>}
  </div>;
}

function projectPulse(workCount:number,questionCount:number,blockingCount:number) {
  if (blockingCount > 0) return { title: "Your input is needed", description: `${blockingCount} open decision${blockingCount === 1 ? " is" : "s are"} blocking the affected work. Review the question and its impact before deciding.` };
  if (questionCount > 0) return { title: "A decision is available", description: "There are open project questions that may shape the direction. Review them when convenient." };
  if (workCount > 0) return { title: "The project plan is recorded", description: "The plan is available below. This prototype has no connected agent, so it cannot report active progress or verified results." };
  return { title: "Your goal is captured", description: "No project work has been planned yet, and no agent is connected. Add an outcome or milestone when you’re ready." };
}

function InboxView({questions,selected,selectedOption,setSelectedOption,note,setNote,busy,choose,answer,reject}:{questions:Question[];selected:QuestionDetail|null;selectedOption:string;setSelectedOption:(value:string)=>void;note:string;setNote:(value:string)=>void;busy:boolean;choose:(id:string)=>void;answer:()=>void;reject:()=>void}) {
  return <div className="inbox-layout"><section className="inbox-questions"><div className="inbox-heading"><span>OPEN DECISIONS</span><span>{questions.length}</span></div>{questions.length ? questions.map((question)=><button key={question.questionId} className={`question-link ${selected?.questionId===question.questionId?"chosen":""}`} onClick={()=>choose(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking?"Your answer is needed to continue":"May affect project direction"}</small></span></button>) : <div className="inbox-empty"><Inbox size={19}/><b>Nothing needs your attention</b><small>No open project decisions.</small></div>}</section><section className="inbox-detail">{selected ? <><div className="eyebrow">{selected.blocking?"YOUR INPUT IS NEEDED":"PROJECT DECISION"}</div><h2>{selected.questionText}</h2><p>{selected.record.impact}</p>{selected.affectedWork.length>0&&<details className="affected-details"><summary>Why this matters <span>{selected.affectedWork.length} affected planned item{selected.affectedWork.length===1?"":"s"}</span></summary><div>{selected.affectedWork.map((item)=><span key={item.workId}>{item.title}</span>)}</div></details>}{selected.record.options.length>0&&<fieldset className="decision-options"><legend>Choose an option</legend>{selected.record.options.map((option)=><label key={option.optionId}><input type="radio" name="decision" value={option.optionId} checked={selectedOption===option.optionId} onChange={()=>setSelectedOption(option.optionId)}/><span><b>{option.label}</b>{option.consequence&&<small>{option.consequence}</small>}</span></label>)}</fieldset>}<label className="note-label">{selected.record.options.length?"ADDITIONAL CONTEXT (OPTIONAL)":"YOUR RESPONSE"}<textarea value={note} onChange={(event)=>setNote(event.target.value)} rows={3} placeholder="Add context for this decision…"/></label><div className="decision-actions"><button className="button secondary" disabled={busy} onClick={reject}>Reject question</button><button className="button primary" disabled={busy} onClick={answer}>{busy?"Saving…":"Record decision"}</button></div><p className="form-hint">Your decision is checked against the current project state.</p></> : <div className="inbox-empty"><Inbox size={19}/><b>Select a decision</b><small>Review its impact and record your direction.</small></div>}</section></div>;
}

function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:React.ReactNode}) { return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17}/></button></div>{children}</section></div>; }

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
