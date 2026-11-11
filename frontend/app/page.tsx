"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, ArrowUpRight, BookOpen, CheckCircle2, ChevronRight, Cpu, Flame, GraduationCap, Layers3, LoaderCircle, LogOut, Map, Pause, Play, RefreshCw, ShieldCheck, Sparkles, Trash2, Zap } from "lucide-react";
import AITerminal from "@/components/AITerminal";
import MapFSM from "@/components/MapFSM";
import { api, message, useAuth, type Dashboard, type GoogleStatus, type Lesson, type Slot, type User } from "@/lib/client";

function Brand() { return <div className="brand"><span className="brand-mark"><Layers3 size={23} /></span><span>SYNAPSE<small>FSM CLUB / ACADEMIC OS</small></span></div>; }

function LoginView() {
  const { login, error: sessionError } = useAuth();
  const [register, setRegister] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    try { await login(String(data.get("email")), String(data.get("password")), register ? String(data.get("name")) : undefined, register ? String(data.get("invite_code")) : undefined); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <main className="auth-layout" id="main"><div className="auth-story"><Brand /><span className="eyebrow">BUILT FOR THE CURIOUS. POWERED BY FSM.</span><h1>Connect the dots.<br /><span>Expand your mind.</span></h1><p>Your campus, courses and curiosity. One intelligent workspace that turns information into understanding.</p><div className="orbital"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit-core"><Cpu size={48} /><span>SYNAPSE / 01</span></div><span className="orbit-node node-a"><BookOpen size={18} /> KNOWLEDGE</span><span className="orbit-node node-b"><Map size={18} /> CAMPUS</span><span className="orbit-node node-c"><Zap size={18} /> MOMENTUM</span></div><p className="auth-footer"><ShieldCheck size={14} /> Private by design. Created for the FSM community.</p></div><section className="panel auth-panel"><span className="eyebrow">YOUR SECOND BRAIN / ONLINE</span><h2>{register ? "Initialize your workspace." : "Welcome back, explorer."}</h2><p className="muted">{register ? "An FSM Club invitation unlocks your academic OS." : "Pick up where your curiosity left off."}</p><form onSubmit={submit}>
    {register && <label>Your name<input name="name" autoComplete="name" minLength={2} maxLength={80} required placeholder="Your name" /></label>}
    <label>Email address<input name="email" type="email" autoComplete="email" maxLength={254} required placeholder="you@university.edu" /></label>
    <label>Password<input name="password" type="password" autoComplete={register ? "new-password" : "current-password"} minLength={register ? 12 : 1} maxLength={128} required placeholder={register ? "At least 12 characters" : "Your password"} /></label>
    {register && <label>FSM Club access code<input name="invite_code" type="password" autoComplete="off" minLength={12} maxLength={128} required placeholder="Provided by the FSM Club" /></label>}
    {(error || sessionError) && <p className="error" role="alert">{error || sessionError}</p>}
    <button className="button-primary w-full" disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : <>{register ? "Create workspace" : "Enter workspace"}<ArrowUpRight size={17} /></>}</button>
  </form><button className="auth-switch" onClick={() => { setRegister(!register); setError(""); }}>{register ? "Already connected? Sign in" : "Have an invitation? Create an account"}</button><div className="auth-security"><ShieldCheck size={15} /><span>Short-lived sessions. Encrypted integrations.<br />Your raw PDFs never touch our disks.</span></div></section></main>;
}

export default function Home() {
  const { user, ready, bootstrap } = useAuth();
  useEffect(() => { void bootstrap(); }, [bootstrap]);
  if (!ready) return <main id="main" className="boot"><Cpu size={42} /><h1>SYNAPSE</h1><span>INITIALIZING WORKSPACE<span className="terminal-cursor">_</span></span></main>;
  if (!user) return <LoginView />;
  // Account changes unmount every lesson, timer and upload view before rendering the next workspace.
  return <Workspace key={user.id} user={user} />;
}

function Workspace({ user }: { user: User }) {
  const { logout } = useAuth();
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const load = useCallback(async () => {
    const account = user.id;
    if (useAuth.getState().user?.id !== account) return;
    const [d, s, l, g] = await Promise.all([api<Dashboard>("/dashboard"), api<Slot[]>("/schedule"), api<Lesson[]>("/documents"), api<GoogleStatus>("/integrations/google/status")]);
    if (useAuth.getState().user?.id !== account) return;
    setDashboard(d); setSlots(s); setLessons(l); setGoogle(g);
  }, [user.id]);
  useEffect(() => {
    load().catch(e => setError(message(e)));
    const id = setInterval(() => load().catch(() => {}), 30000);
    return () => clearInterval(id);
  }, [load]);
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, []);
  useEffect(() => {
    const result = new URLSearchParams(location.search).get("google");
    if (result) { setNotice(result === "connected" ? "Google Classroom connected. Automatic sync is now queued." : "Google connection failed. Please reconnect and grant the requested permissions."); history.replaceState(null, "", location.pathname); }
  }, []);
  async function action(run: () => Promise<void>) { setBusy(true); setError(""); try { await run(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function focus() {
    if (dashboard?.active_session) await api(`/study/${dashboard.active_session.id}/stop`, { method: "POST" });
    else await api("/study/start", { method: "POST" });
    await load();
  }
  async function openLesson(id: string) {
    const account = user.id;
    if (useAuth.getState().user?.id !== account) return;
    const result = await api<Lesson>(`/documents/${id}`);
    if (result.status !== "ready") throw new Error(["failed", "cleanup_pending"].includes(result.status) ? "Generation failed. Delete this entry and upload again, or wait for the Classroom retry." : "This lesson is still processing.");
    if (useAuth.getState().user?.id !== account) return;
    setLesson(result); document.getElementById("terminal")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function removeLesson(item: Lesson) {
    if (!window.confirm(`Delete “${item.title}” and its embeddings? This cannot be undone.`)) return;
    await api(`/documents/${item.id}`, { method: "DELETE" }); if (lesson?.id === item.id) setLesson(null); await load();
  }
  const focusSeconds = dashboard?.active_session ? Math.min(7200, Math.max(0, Math.floor((now - Date.parse(dashboard.active_session.started_at)) / 1000))) : 0;
  const displayDate = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: dashboard?.timezone || "Africa/Tunis" }).format(now);
  return <div className="app-shell"><aside className="sidebar"><Brand /><span className="nav-caption">WORKSPACE</span><nav aria-label="Main navigation"><a href="#overview" className="active"><Layers3 size={18} />Overview<span>01</span></a><a href="#terminal"><Sparkles size={18} />AI studio<span>02</span></a><a href="#library"><BookOpen size={18} />Knowledge vault<span>03</span></a><a href="#campus"><Map size={18} />Campus map<span>04</span></a><a href="#classroom"><GraduationCap size={18} />Classroom<span>05</span></a></nav><div className="sidebar-bottom"><div className="club-card"><span className="eyebrow">A LITTLE EVERY DAY.</span><strong>Big ideas start<br />with small steps.</strong><span className="text-teal">Keep your momentum ↗</span></div><div className="profile"><span className="avatar">{user.name.slice(0, 2).toUpperCase()}</span><div><strong>{user.name}</strong><small>FSM explorer</small></div><button className="icon-button" aria-label="Sign out" disabled={busy} onClick={() => action(logout)}><LogOut size={17} /></button></div></div></aside>
    <div className="workspace"><header className="topbar"><span><span className="text-teal">WORKSPACE</span><ChevronRight size={13} />Overview</span><div><span className="system-label"><i className="status-dot" /> {dashboard ? "SYSTEMS CONNECTED" : "CONNECTING"}</span><span className="tag">FSM CLUB</span><button className="icon-button mobile-logout" onClick={() => action(logout)} aria-label="Sign out"><LogOut size={17} /></button></div></header>
    <main id="main" className="main-content"><section id="overview" className="welcome"><div><span className="eyebrow">YOUR ACADEMIC COMMAND CENTER</span><h1>A little more <span>connected.</span></h1><p>Welcome back, {user.name.split(" ")[0]}. Let’s make room for your next big idea.</p></div><div className="date-block"><Activity size={15} /><span>{displayDate}<small>{dashboard?.timezone || "Africa/Tunis"}</small></span></div></section>
    {error && <div className="banner error" role="alert">{error}<button className="text-button" onClick={() => action(load)}>Retry</button></div>}{notice && <div className="banner" role="status">{notice}<button className="text-button" onClick={() => setNotice("")}>Dismiss</button></div>}
    <div className="stats-grid">{[{ label: "FOCUS TODAY", value: dashboard?.today_minutes ?? 0, unit: "min", icon: <Activity size={18} />, note: "Time invested in yourself" }, { label: "DAILY STREAK", value: dashboard?.streak ?? 0, unit: "days", icon: <Flame size={18} />, note: "Consistency builds confidence" }, { label: "LESSONS COMPLETED", value: dashboard?.completed_lessons ?? 0, unit: "", icon: <CheckCircle2 size={18} />, note: "Knowledge, made your own" }, { label: "SYNAPSE XP", value: dashboard?.xp ?? 0, unit: "XP", icon: <Zap size={18} />, note: "Your curiosity has momentum" }].map((stat, i) => <article className={`stat-card stat-${i}`} key={stat.label}><div><span>{stat.label}</span>{stat.icon}</div><strong>{stat.value.toLocaleString()}<small>{stat.unit}</small></strong><p>{stat.note}</p></article>)}</div>
    <div className="workspace-grid"><div className="main-column"><AITerminal lesson={lesson} onLesson={setLesson} onChange={load} /><section className="panel" id="library"><div className="panel-heading"><div><span className="eyebrow">03 / YOUR KNOWLEDGE VAULT</span><h2>Ideas worth keeping.</h2></div><span className="tag">{lessons.length} MATERIALS</span></div>{lessons.length ? <div className="lesson-list">{lessons.map(item => <div className="library-row" key={item.id}><span className="file-icon"><BookOpen size={18} /></span><button onClick={() => action(() => openLesson(item.id))} disabled={busy}><strong>{item.title}</strong><small>{item.source === "classroom" ? "Google Classroom" : "Manual upload"} · {item.status} {item.completed ? "· Completed" : ""}</small></button>{item.completed && <CheckCircle2 size={16} className="text-cyan" />}<button className="icon-button delete-button" aria-label={`Delete ${item.title}`} disabled={busy || item.status === "processing"} onClick={() => action(() => removeLesson(item))}><Trash2 size={15} /></button></div>)}</div> : <div className="empty-state"><BookOpen size={24} /><p>A clear space for your next discovery.</p><small>Upload a chapter above or connect Google Classroom.</small></div>}</section></div>
    <div className="side-column"><section className="panel focus-panel"><div className="subheading"><span className="eyebrow">STAY IN YOUR FLOW</span><span className="tag">FOCUS MODE</span></div><div className="focus-clock">{String(Math.floor(focusSeconds / 60)).padStart(2, "0")}<span>:</span>{String(focusSeconds % 60).padStart(2, "0")}</div><p>{dashboard?.active_session ? "One thought at a time. You’re doing great." : "A quiet moment. A clearer mind."}</p><button className="button-primary w-full" disabled={busy || !dashboard} onClick={() => action(focus)}>{dashboard?.active_session ? <><Pause size={15} />Finish & save session</> : <><Play size={15} />Start a focus session</>}</button><div className="activity-chart" aria-label="Study minutes in the past seven days">{dashboard?.week.map(day => <div key={day.date} title={`${day.date}: ${day.minutes} minutes, ${day.lessons} lessons`}><div className="bar-track"><span style={{ height: `${Math.max(3, Math.min(100, day.minutes / 120 * 100))}%` }} /></div><small>{new Date(day.date + "T12:00:00Z").toLocaleDateString("en", { weekday: "narrow", timeZone: "UTC" })}</small></div>)}</div><p className="fine-print">Last 7 days · 120 min scale · Sessions capped at 2 hours</p></section>
    <MapFSM schedule={slots} timezone={dashboard?.timezone || "Africa/Tunis"} onChange={load} />
    <section className="panel classroom-panel" id="classroom"><div className="integration-icon"><GraduationCap size={24} /></div><span className="eyebrow">AUTOMATE YOUR LEARNING</span><h2>Your classroom.<br />Always in sync.</h2><p>New PDF course materials become study packs automatically. No download shuffle.</p><p className="fine-print">Requires read-only access to your courses, course materials and Drive files. Only PDF attachments in Classroom materials are imported, up to 3 per poll.</p>{google?.connected ? <><span className="connection-status"><i className="status-dot" />{google.syncing ? "SYNC IN PROGRESS" : "CLASSROOM CONNECTED"}</span><p className="fine-print">{google.last_sync_at ? `Last checked ${new Date(google.last_sync_at).toLocaleString()}` : "First sync queued"}</p>{google.error && <p className="error" role="alert">{google.error}</p>}<button className="button-secondary w-full" disabled={busy || google.syncing} onClick={() => action(async () => { await api("/integrations/google/sync", { method: "POST" }); setNotice("Classroom sync queued. The worker will check for new PDFs shortly."); await load(); })}><RefreshCw size={15} />Sync now</button><button className="text-button disconnect" disabled={busy} onClick={() => action(async () => { if (confirm("Remove stored Google credentials? Your generated lessons will remain.")) { await api("/integrations/google/disconnect", { method: "DELETE" }); await load(); } })}>Disconnect Classroom</button></> : <button className="button-secondary w-full" disabled={busy || !google?.configured} onClick={() => action(async () => { const result = await api<{ url: string }>("/integrations/google/connect", { method: "POST" }); window.location.assign(result.url); })}>{google?.configured ? <>Connect Classroom <ArrowUpRight size={16} /></> : "Google OAuth setup required"}</button>}</section>
    </div></div><footer className="workspace-footer"><span>SYNAPSE <span className="text-teal">/</span> BUILT FOR FSM. BUILT FOR WHAT’S NEXT.</span><span><ShieldCheck size={12} />PRIVATE WORKSPACE</span></footer></main></div>
  </div>;
}
