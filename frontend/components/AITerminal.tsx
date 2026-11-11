"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowUpRight, Check, FileUp, LoaderCircle, Send, Sparkles, Terminal, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { api, message, type Lesson } from "@/lib/client";

type Tab = "summary" | "quiz" | "flashcards" | "ask";
function Markdown({ text, animate = false }: { text: string; animate?: boolean }) {
  const reduced = useReducedMotion();
  const [length, setLength] = useState(animate && !reduced ? 0 : text.length);
  useEffect(() => {
    if (!animate || reduced) { setLength(text.length); return; }
    setLength(0); const id = setInterval(() => setLength(n => { if (n >= text.length) clearInterval(id); return Math.min(text.length, n + 60); }), 20);
    return () => clearInterval(id);
  }, [text, animate, reduced]);
  return <div className="markdown"><ReactMarkdown skipHtml components={{ a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>{text.slice(0, length)}</ReactMarkdown>{length < text.length && <span className="terminal-cursor">▌</span>}</div>;
}

export default function AITerminal({ lesson, onLesson, onChange }: { lesson: Lesson | null; onLesson: (lesson: Lesson | null) => void; onChange: () => Promise<void> }) {
  const [tab, setTab] = useState<Tab>("summary");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [answers, setAnswers] = useState<number[]>([-1, -1, -1, -1, -1]);
  const [score, setScore] = useState<{ score: number; xp_awarded: number; passed: boolean } | null>(null);
  const [flipped, setFlipped] = useState<number[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ answer: string; sources: { id: number; page: number; excerpt: string }[] } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setTab("summary"); setAnswers([-1, -1, -1, -1, -1]); setScore(null); setFlipped([]); setAnswer(null); setError(""); }, [lesson?.id]);
  async function upload(file?: File) {
    if (!file || busy) return;
    if (!file.name.toLowerCase().endsWith(".pdf") || file.size > 10 * 1024 * 1024 || !file.size) { setError("Choose a PDF up to 10 MiB."); return; }
    setBusy(true); setError("");
    try { const result = await api<Lesson>("/ai/generate", { method: "POST", headers: { "Content-Type": "application/pdf", "X-File-Name": encodeURIComponent(file.name) }, body: file });
      if (alive.current) { onLesson(result); await onChange(); } }
    catch (e) { if (alive.current) setError(message(e)); }
    finally { if (alive.current) setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function grade() {
    if (!lesson || answers.includes(-1)) return;
    setBusy(true); setError("");
    try { setScore(await api(`/documents/${lesson.id}/attempts`, { method: "POST", json: { answers } })); await onChange(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function ask(event: React.FormEvent) {
    event.preventDefault(); if (!lesson) return;
    setBusy(true); setError("");
    try { setAnswer(await api(`/documents/${lesson.id}/ask`, { method: "POST", json: { question } })); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  const pages = (ids: number[]) => [...new Set(ids.map(id => lesson?.pages?.[id - 1]).filter(Boolean))].join(", ");
  return <section className="panel terminal-panel" id="terminal" aria-labelledby="terminal-title">
    <div className="panel-heading"><div><span className="eyebrow">01 / SYNAPSE INTELLIGENCE</span><h2 id="terminal-title">Less noise. More knowledge<span className="text-cyan">.</span></h2></div><Sparkles size={23} className="text-cyan" /></div>
    <div className="terminal-bar"><span><i className="dot" /><i className="dot" /><i className="dot" /></span><span><Terminal size={12} /> synapse / study-engine</span><span className="text-cyan">{busy ? "PROCESSING" : "READY"}</span></div>
    <input ref={fileRef} type="file" accept="application/pdf,.pdf" className="sr-only" aria-label="Upload course PDF" onChange={e => upload(e.target.files?.[0])} disabled={busy} />
    {!lesson?.pack && <div className={`upload-zone ${drag ? "dragging" : ""}`} onDragOver={e => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files[0]); }}>
      <div className="upload-glyph">{busy ? <LoaderCircle size={30} className="spin" /> : <FileUp size={30} />}</div><h3>{busy ? "Connecting the dots…" : "Drop a chapter. Unlock understanding."}</h3>
      <p>{busy ? "Extracting text → building context → generating your study pack." : "Your lecture notes, transformed into clear summaries, quizzes and flashcards."}</p>
      <button className="button-primary" disabled={busy} onClick={() => fileRef.current?.click()}>{busy ? "Generating study pack…" : <>Upload a PDF <ArrowUpRight size={16} /></>}</button>
      <small>PDF up to 10 MiB · Text-based, 80 pages max · No raw file storage</small>
    </div>}
    {lesson?.pack && <>
      <div className="lesson-heading"><div><span className="eyebrow">CONTEXT LOADED / {lesson.source.toUpperCase()}</span><h3>{lesson.pack.title}</h3></div><button className="icon-button" disabled={busy} aria-label="Close lesson and upload another" onClick={() => onLesson(null)}><X size={18} /></button></div>
      <div className="tabs" role="tablist" aria-label="Study modes">{(["summary", "quiz", "flashcards", "ask"] as Tab[]).map(t => <button role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls="study-content" key={t} onClick={() => setTab(t)}>{t === "ask" ? "Ask Synapse" : t}{t === "quiz" && <span>05</span>}{t === "flashcards" && <span>03</span>}</button>)}</div>
      <AnimatePresence mode="wait"><motion.div key={`${lesson.id}-${tab}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: .15 }} id="study-content" role="tabpanel" aria-labelledby={`tab-${tab}`} className="study-content">
        {tab === "summary" && <><p className="terminal-command"><span>$</span> summarize --source &quot;{lesson.filename}&quot;</p><Markdown text={lesson.pack.summary_markdown} animate /><p className="fine-print">AI-generated study aid. Verify formulas and conclusions against your original course.</p></>}
        {tab === "quiz" && <><p className="fine-print">Five questions. Score at least 3/5 to complete this lesson and earn 50 XP once.</p>{lesson.pack.questions.map((q, i) => <fieldset key={i} className="quiz-question"><legend><span className="text-cyan">0{i + 1} / </span>{q.question}</legend>{q.choices.map((choice, index) => <label key={index} className={`quiz-option ${answers[i] === index ? "chosen" : ""} ${score && index === q.correct_index ? "correct" : ""}`}><input type="radio" name={`question-${i}`} value={index} checked={answers[i] === index} disabled={!!score || busy} onChange={() => setAnswers(prev => prev.map((a, j) => i === j ? index : a))} /><span>{String.fromCharCode(65 + index)}</span>{choice}{score && index === q.correct_index && <Check size={14} />}</label>)}{score && <p className="quiz-explanation">{q.explanation}<small>Source pages: {pages(q.source_chunks)}</small></p>}</fieldset>)}{score ? <div className="score-box" role="status"><strong>{score.score}/5</strong><span>{score.passed ? "Lesson complete" : "Keep exploring the material"} · +{score.xp_awarded} XP</span><button className="text-button" onClick={() => { setScore(null); setAnswers([-1, -1, -1, -1, -1]); }}>Try again</button></div> : <button className="button-primary" onClick={grade} disabled={busy || answers.includes(-1)}>{busy ? "Checking…" : "Check my understanding"}</button>}</>}
        {tab === "flashcards" && <div className="flashcards">{lesson.pack.flashcards.map((card, i) => <button className={`flashcard ${flipped.includes(i) ? "flipped" : ""}`} key={i} onClick={() => setFlipped(prev => prev.includes(i) ? prev.filter(n => n !== i) : [...prev, i])} aria-label={`Card ${i + 1}: ${flipped.includes(i) ? 'answer shown, reveal question' : 'reveal answer'}`}><span className="eyebrow">CARD 0{i + 1} / {flipped.includes(i) ? "ANSWER" : "QUESTION"}</span><strong>{flipped.includes(i) ? card.back : card.front}</strong><small>{flipped.includes(i) ? `Source pages ${pages(card.source_chunks)}` : "Click to reveal ↗"}</small></button>)}</div>}
        {tab === "ask" && <><p className="fine-print">Answers grounded in this lesson only. Uses the shared limit of 10 AI requests per hour.</p><form onSubmit={ask} className="ask-form"><label htmlFor="ask-input" className="sr-only">Question about this lesson</label><input id="ask-input" value={question} onChange={e => setQuestion(e.target.value)} minLength={3} maxLength={2000} placeholder="What connects these ideas?" required /><button className="button-primary" type="submit" disabled={busy} aria-label="Send question">{busy ? <LoaderCircle size={17} className="spin" /> : <Send size={17} />}</button></form>{answer && <div className="rag-answer"><Markdown text={answer.answer} />{answer.sources.map(source => <details key={source.id}><summary>Source [{source.id}] · page {source.page}</summary><p>{source.excerpt}</p></details>)}</div>}</>}
      </motion.div></AnimatePresence>
    </>}
    {error && <p role="alert" className="error terminal-error">{error}</p>}
    <div className="terminal-footer"><span><i className="status-dot" /> SOURCE-GROUNDED AI</span><span>ENCRYPTED CONNECTION / RAM-ONLY PDF INGESTION</span></div>
  </section>;
}
