import React from "react";
import { createRoot } from "react-dom/client";
import {
  ChevronDown, CircleHelp, Folder, FolderKanban, Inbox,
  Layers3, Plus, Search, ShieldAlert, Sparkles, X,
} from "lucide-react";
import "./style.css";
import "./goal.css";

type Project = { projectId: string; name: string; status: string; charter: { revision: number; goals: string[]; nonGoals: string[]; acceptanceCriteria: string[] } };
type Work = { workId: string; title: string; objective: string; workType: string; status: string; parentWorkId?: string | null; dependsOn: string[]; acceptanceCriteria: string[]; revision: number };
type ProjectEvent = { eventId: string; title: string; description: string; actorLabel: string; occurredAt: string };
type Summary = { project: Project; workItems: Work[]; recentEvents: ProjectEvent[]; counts: Record<string, number> };
type Question = { questionId: string; questionType: string; questionText: string; priority: string; blocking: boolean; workIds: string[]; etag: string };
type QuestionDetail = Question & { record: { questionType: string; options: { optionId: string; label: string; consequence?: string }[]; impact: string; status: string; questionText: string }; affectedWork: { workId: string; title: string; status: string }[] };
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
  const alignmentQuestionCount = questions.filter((question) => question.questionType === "clarification").length;
  const alignmentWork = workItems.find((item) => item.workId === "work.alignment-intake");
  const alignmentPending = alignmentQuestionCount > 0 || Boolean(alignmentWork && !["completed", "cancelled"].includes(alignmentWork.status));
  const pulse = projectPulse(workItems.length, openQuestionCount, blockingQuestionCount, alignmentQuestionCount, alignmentWork);

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
      <header className="goal-topbar"><div className="goal-topbar-title"><span className="project-dot"/><div><h1>{view === "inbox" ? "Your input" : project?.name || "Project"}</h1><small>{view === "inbox" ? "Decisions that need your attention" : "Goal, outcomes, and what needs attention"}</small></div></div><div className="goal-topbar-actions"><span className="prototype-status"><ShieldAlert size={13}/>Prototype · test data only</span>{view === "overview" && (alignmentPending ? <button className="button secondary" onClick={() => setView("inbox")}><Inbox size={14}/>Review alignment</button> : <button className="button primary" onClick={() => setWorkDialog(true)}><Plus size={14}/>Add outcome or step</button>)}</div></header>
      <div className="goal-stage">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {view === "inbox" ? <InboxView questions={questions} selected={selectedQuestion} selectedOption={selectedOption} setSelectedOption={setSelectedOption} note={answerNote} setNote={setAnswerNote} busy={decisionBusy} choose={chooseQuestion} answer={() => void submitDecision(false)} reject={() => void submitDecision(true)}/> : project ? <div className="goal-scroll">
          <section className="goal-hero"><div className="eyebrow"><Sparkles size={13}/> {alignmentPending ? "PROVISIONAL REPOSITORY HYPOTHESIS" : "PROJECT DIRECTION"}</div><h2>{project.charter.goals.join(" ") || "No project goal defined"}</h2>{alignmentPending && <p className="goal-context-note">This wording comes from the repository, not from a confirmed conversation with you. It is not yet the agreed overarching vision.</p>}<div className="goal-project-meta"><span>{project.name}</span><span>Charter revision {project.charter.revision}</span></div></section>
          <section className={`project-pulse ${openQuestionCount ? "needs-input" : ""}`} aria-labelledby="pulse-title"><div className="pulse-icon"><Sparkles size={17}/></div><div className="pulse-copy"><div className="eyebrow">PROJECT STATE</div><h3 id="pulse-title">{pulse.title}</h3><p>{pulse.description}</p></div>{openQuestionCount > 0 && <button className="button secondary" onClick={() => setView("inbox")}>Your input <span aria-hidden="true">→</span></button>}</section>
          <div className="goal-columns">
            <section className="goal-card outcome-card"><div className="goal-card-heading"><div><div className="eyebrow">PROJECT BREAKDOWN</div><h3>Goal → outcomes → workstreams → steps</h3></div><Layers3 size={17}/></div>{alignmentPending ? <p className="empty-copy">No outcome or implementation breakdown has been agreed. First align on the real vision and how you want the work handled; then I’ll propose a hierarchy for your review before anything starts.</p> : <WorkTree items={workItems.filter((item) => item.workId !== "work.alignment-intake")} />}</section>
            <section className="goal-card attention-card"><div className="goal-card-heading"><div><div className="eyebrow">YOUR INPUT</div><h3>{openQuestionCount ? `${openQuestionCount} ${openQuestionCount === 1 ? "question" : "questions"} to answer` : "No open project questions"}</h3></div><Inbox size={17}/></div>{questions.length ? <div className="attention-list">{questions.slice(0,3).map((question)=><button key={question.questionId} className="attention-item" onClick={()=>void openQuestion(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking ? "Needed before the alignment phase can continue" : "May shape the project direction"}</small></span><span aria-hidden="true">→</span></button>)}</div> : <p className="empty-copy">Project tasks and decisions will appear here when they are actually agreed or need your input.</p>}</section>
          </div>
          <details className="detail-disclosure"><summary><span><b>Process and work-item details</b><small>Proposals, statuses, and implementation detail</small></span><span className="detail-count">{workItems.length}</span></summary>{workItems.length ? <div className="plan-list">{workItems.map((item)=><article className="plan-item" key={item.workId}><div className="plan-item-title"><span className={`thread-status-dot ${item.status}`}/><b>{item.title}</b><span className={`work-status ${item.status}`}><i/>{item.status.replaceAll("_"," ")}</span></div><p>{item.objective}</p><small>{item.workType} · revision {item.revision}{item.parentWorkId ? ` · parent ${workItems.find((parent)=>parent.workId===item.parentWorkId)?.title || item.parentWorkId}` : ""}</small></article>)}</div> : <p className="empty-copy">No work items have been agreed.</p>}</details>
          {project.charter.acceptanceCriteria.length > 0 && <details className="detail-disclosure"><summary><span><b>Recorded acceptance criteria</b><small>Stored wording only; nothing is automatically verified</small></span><span className="detail-count">{project.charter.acceptanceCriteria.length}</span></summary><ul className="criteria-list">{project.charter.acceptanceCriteria.map((criterion,index)=><li key={`${index}-${criterion}`}>{criterion}</li>)}</ul></details>}
          <details className="detail-disclosure history-disclosure"><summary><span><b>Project history</b><small>Event-by-event detail, kept out of the main progress view</small></span><span className="detail-count">{summary?.recentEvents.length || 0}</span></summary>{summary?.recentEvents.length ? <ol className="history-list">{summary.recentEvents.map((event)=><li key={event.eventId}><span><b>{event.title}</b><small>{event.description}</small><time>{event.actorLabel} · {new Date(event.occurredAt).toLocaleString()}</time></span></li>)}</ol> : <p className="empty-copy">No project events have been recorded yet.</p>}</details>
          <div className="goal-footnote"><ShieldAlert size={13}/>No project-scoped model or worker is connected. This prototype does not launch installed CLI tools or report agent progress or verified outcomes.</div>
        </div> : <div className="loading">Loading your project…</div>}
      </div>
    </main>
    {projectDialog && <Modal title="Create a project" onClose={() => setProjectDialog(false)}><form onSubmit={createProject}><label>Project name<input name="name" required maxLength={100} autoFocus placeholder="e.g. Reading list"/></label><label>What do you want to accomplish?<textarea name="goal" required maxLength={1000} rows={3} placeholder="What do you want to be true when this succeeds?"/></label><div className="form-actions"><button type="button" className="button secondary" onClick={() => setProjectDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Creating…" : "Create project"}</button></div></form></Modal>}
    {workDialog && <Modal title="Add an outcome or nested step" onClose={() => setWorkDialog(false)}><form onSubmit={createWork}><label>Meaningful result<input name="title" required maxLength={160} autoFocus placeholder="Describe the outcome or workstream"/></label><label>Why does it advance the project goal?<textarea name="objective" required maxLength={2000} rows={3} placeholder="Describe the result, not an agent's internal checklist."/></label><label>Parent outcome or workstream<select name="parentWorkId"><option value="">Top-level project outcome</option>{workItems.filter((item)=>item.workId!=="work.alignment-intake"&&item.status!=="cancelled").map((item)=><option key={item.workId} value={item.workId}>{item.title} · {item.status.replaceAll("_"," ")}</option>)}</select></label><label>Category<select name="workType"><option value="research">Research</option><option value="analysis">Analysis</option><option value="planning">Planning</option><option value="implementation">Implementation</option><option value="review">Review</option><option value="writing">Writing</option><option value="operations">Operations</option></select></label><label>Acceptance criteria (optional, one per line)<textarea name="acceptanceCriteria" maxLength={4000} rows={2} placeholder="Only add criteria you actually want to judge the result by."/></label><p className="form-hint">This records a proposal in the project hierarchy; it does not start an agent or claim progress.</p><div className="form-actions"><button type="button" className="button secondary" onClick={() => setWorkDialog(false)}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Saving…" : "Add proposal"}</button></div></form></Modal>}
  </div>;
}

function projectPulse(workCount:number,questionCount:number,blockingCount:number,alignmentQuestionCount:number,alignmentWork?:Work) {
  if (alignmentQuestionCount > 0) return { title: "Your project direction needs alignment", description: "The repository supplied a hypothesis, not your confirmed vision. No research or implementation breakdown is agreed; answer the alignment question and I’ll propose an outcome hierarchy for your review." };
  if (alignmentWork && !["completed", "cancelled"].includes(alignmentWork.status)) return { title: "Alignment is not complete yet", description: "Your response is recorded. The repo needs to be reconciled with your direction, then a proposed goal-to-outcome breakdown must be reviewed before delivery work is added." };
  if (blockingCount > 0) return { title: "Your input is needed", description: `${blockingCount} open question${blockingCount === 1 ? " is" : "s are"} blocking affected work. Review the impact before deciding.` };
  if (questionCount > 0) return { title: "A decision may shape the project", description: "There are open project questions to review; no progress is inferred from the plan." };
  if (workCount > 0) return { title: "Outcomes and work are recorded", description: "The hierarchy shows the current proposals and status. This prototype has no connected agent, so it cannot report active progress or verified results." };
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

function InboxView({questions,selected,selectedOption,setSelectedOption,note,setNote,busy,choose,answer,reject}:{questions:Question[];selected:QuestionDetail|null;selectedOption:string;setSelectedOption:(value:string)=>void;note:string;setNote:(value:string)=>void;busy:boolean;choose:(id:string)=>void;answer:()=>void;reject:()=>void}) {
  return <div className="inbox-layout"><section className="inbox-questions"><div className="inbox-heading"><span>OPEN DECISIONS</span><span>{questions.length}</span></div>{questions.length ? questions.map((question)=><button key={question.questionId} className={`question-link ${selected?.questionId===question.questionId?"chosen":""}`} onClick={()=>choose(question.questionId)}><span className={`question-dot ${question.priority}`}/><span><b>{question.questionText}</b><small>{question.blocking?"Your answer is needed to continue":"May affect project direction"}</small></span></button>) : <div className="inbox-empty"><Inbox size={19}/><b>Nothing needs your attention</b><small>No open project decisions.</small></div>}</section><section className="inbox-detail">{selected ? <><div className="eyebrow">{selected.blocking?"YOUR INPUT IS NEEDED":"PROJECT DECISION"}</div><h2>{selected.questionText}</h2><p>{selected.record.impact}</p>{selected.affectedWork.length>0&&<details className="affected-details"><summary>Why this matters <span>{selected.affectedWork.length} affected planned item{selected.affectedWork.length===1?"":"s"}</span></summary><div>{selected.affectedWork.map((item)=><span key={item.workId}>{item.title}</span>)}</div></details>}{selected.record.options.length>0&&<fieldset className="decision-options"><legend>Choose an option</legend>{selected.record.options.map((option)=><label key={option.optionId}><input type="radio" name="decision" value={option.optionId} checked={selectedOption===option.optionId} onChange={()=>setSelectedOption(option.optionId)}/><span><b>{option.label}</b>{option.consequence&&<small>{option.consequence}</small>}</span></label>)}</fieldset>}<label className="note-label">{selected.record.options.length?"ADDITIONAL CONTEXT (OPTIONAL)":"YOUR RESPONSE"}<textarea value={note} onChange={(event)=>setNote(event.target.value)} rows={3} placeholder={selected.record.options.length ? "Add context for this decision…" : "Write your response; partial answers are fine."}/></label><div className="decision-actions"><button className="button secondary" disabled={busy} onClick={reject}>Reject question</button><button className="button primary" disabled={busy} onClick={answer}>{busy?"Saving…":selected.record.questionType==="clarification"?"Record response":"Record decision"}</button></div><p className="form-hint">{selected.record.questionType==="clarification"?"Your response is saved as project input; it does not approve execution or start an agent.":"Your decision is checked against the current project state."}</p></> : <div className="inbox-empty"><Inbox size={19}/><b>Select a decision</b><small>Review its impact and record your direction.</small></div>}</section></div>;
}

function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:React.ReactNode}) { return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-head"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={17}/></button></div>{children}</section></div>; }

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
