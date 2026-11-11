"use client";

import { useEffect, useState } from "react";
import { Crosshair, MapPin, Plus, Trash2 } from "lucide-react";
import { api, campusTime, clock, message, type Slot } from "@/lib/client";

const rooms = [
  { id: "A101", name: "Lecture room", x: 28, y: 44, w: 135, h: 82 },
  { id: "A102", name: "Lecture room", x: 173, y: 44, w: 135, h: 82 },
  { id: "LAB1", name: "Physics lab", x: 28, y: 180, w: 135, h: 82 },
  { id: "LAB2", name: "Computing lab", x: 173, y: 180, w: 135, h: 82 },
  { id: "A201", name: "Upper floor", x: 368, y: 44, w: 135, h: 82 },
  { id: "A202", name: "Upper floor", x: 513, y: 44, w: 135, h: 82 },
  { id: "LIB", name: "Library", x: 368, y: 180, w: 135, h: 82 },
  { id: "AMPHI", name: "Amphitheatre", x: 513, y: 180, w: 135, h: 82 }
];
const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function MapFSM({ schedule, timezone, onChange }: { schedule: Slot[]; timezone: string; onChange: () => Promise<void> }) {
  const [now, setNow] = useState(new Date());
  const [selected, setSelected] = useState("A101");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 15000); return () => clearInterval(id); }, []);
  const { day, minute } = campusTime(now, timezone);
  const current = schedule.find(s => s.weekday === day && s.start_minute <= minute && s.end_minute > minute);
  const today = schedule.filter(s => s.weekday === day);
  const selectedRoom = rooms.find(r => r.id === selected)!;
  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    const toMinutes = (value: FormDataEntryValue | null) => { const [h, m] = String(value).split(":").map(Number); return h * 60 + m; };
    try { await api("/schedule", { method: "POST", json: { course: form.get("course"), room: form.get("room"), weekday: Number(form.get("weekday")), start_minute: toMinutes(form.get("start")), end_minute: toMinutes(form.get("end")) } }); await onChange(); setEditing(false); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true); setError("");
    try { await api(`/schedule/${id}`, { method: "DELETE" }); await onChange(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <section className="panel" id="campus" aria-labelledby="campus-title">
    <div className="panel-heading"><div><span className="eyebrow">02 / SPATIAL INTELLIGENCE</span><h2 id="campus-title">Your campus, decoded<span className="text-cyan">.</span></h2></div><Crosshair size={22} className="text-teal" /></div>
    <div className="map-toolbar"><span><i className="status-dot" /> {current ? `IN SESSION / ${current.room}` : "NO CLASS IN SESSION"}</span><button className="text-button" onClick={() => current && setSelected(current.room)} disabled={!current}>Locate class <MapPin size={13} /></button></div>
    <div className="map-surface"><svg viewBox="0 0 680 300" role="group" aria-label="Illustrative FSM campus room schematic">
      <defs><pattern id="map-grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 20" fill="none" stroke="#66FCF1" strokeOpacity=".07" /></pattern></defs>
      <rect width="680" height="300" fill="url(#map-grid)" />
      <path d="M 15 153 H 665 M 337 25 V 278" stroke="#45A29E" strokeWidth="2" strokeDasharray="6 6" opacity=".4" />
      <text x="28" y="24" className="map-label">TEACHING / GROUND</text><text x="368" y="24" className="map-label">TEACHING / UPPER + COMMONS</text>
      {rooms.map(room => <g key={room.id} tabIndex={0} role="button" aria-label={`${room.id}, ${room.name}${current?.room === room.id ? ', current class' : ''}`} aria-pressed={selected === room.id}
        onClick={() => setSelected(room.id)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(room.id); } }} className={`map-room ${selected === room.id ? "selected" : ""} ${current?.room === room.id ? "active" : ""}`}>
        <rect x={room.x} y={room.y} width={room.w} height={room.h} rx="3" />
        <text x={room.x + 14} y={room.y + 32} className="room-code">{room.id}</text><text x={room.x + 14} y={room.y + 55} className="room-name">{room.name}</text>
        {current?.room === room.id && <circle cx={room.x + room.w - 16} cy={room.y + 20} r="4" fill="#FF007F" />}
      </g>)}
      <text x="28" y="287" className="map-label">SCHEMATIC / NOT TO SCALE</text><text x="589" y="287" className="map-label">N ↑</text>
    </svg></div>
    <div className="room-detail"><MapPin size={16} /><strong>{selectedRoom.id}</strong><span>{selectedRoom.name}</span><span className="tag">{schedule.filter(s => s.room === selected).length} scheduled</span></div>
    <p className="fine-print">Illustrative room data, not an official FSM floor plan. Confirm locations with the faculty before navigating.</p>
    <div className="subheading"><h3>Timetable <span className="text-teal">/ {days[day]}</span></h3><button className="text-button" onClick={() => setEditing(!editing)}><Plus size={14} /> Add class</button></div>
    {today.length ? today.map(slot => <div className="schedule-row" key={slot.id}><time>{clock(slot.start_minute)}<small>{clock(slot.end_minute)}</small></time><div><strong>{slot.course}</strong><small>{slot.room} · {current?.id === slot.id ? "In session" : "Today"}</small></div><button className="icon-button" disabled={busy} aria-label={`Delete ${slot.course}`} onClick={() => remove(slot.id)}><Trash2 size={14} /></button></div>) : <p className="empty-text">No classes today. Your time is yours.</p>}
    {editing && <form onSubmit={add} className="schedule-form">
      <label>Subject<input name="course" minLength={2} maxLength={100} placeholder="e.g. Linear algebra" required /></label>
      <div className="form-grid"><label>Day<select name="weekday" defaultValue={day}>{days.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></label><label>Room<select name="room" defaultValue={selected}>{rooms.map(r => <option key={r.id}>{r.id}</option>)}</select></label></div>
      <div className="form-grid"><label>Starts<input name="start" type="time" required defaultValue="09:00" /></label><label>Ends<input name="end" type="time" required defaultValue="10:30" /></label></div><button className="button-primary" disabled={busy}>{busy ? "Saving…" : "Save class"}</button>
    </form>}
    {schedule.some(s => s.weekday !== day) && <details className="weekly"><summary>Full week · {schedule.length} classes</summary>{schedule.map(s => <div className="week-slot" key={s.id}><span>{days[s.weekday]} {clock(s.start_minute)} / {s.course} / {s.room}</span><button className="icon-button" disabled={busy} onClick={() => remove(s.id)} aria-label={`Delete ${s.course} on ${days[s.weekday]}`}><Trash2 size={13} /></button></div>)}</details>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
