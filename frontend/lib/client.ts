import { create } from "zustand";

export type User = { id: string; name: string; email: string };
export type Slot = { id: string; course: string; room: string; weekday: number; start_minute: number; end_minute: number };
export type Question = { question: string; choices: string[]; correct_index: number; explanation: string; source_chunks: number[] };
export type Lesson = { id: string; title: string; filename: string; source: string; status: string; best_score: number; completed: boolean; created_at: string;
  pages?: number[]; pack?: { title: string; summary_markdown: string; questions: Question[]; flashcards: { front: string; back: string; source_chunks: number[] }[] } };
export type Dashboard = { timezone: string; today_minutes: number; streak: number; completed_lessons: number; xp: number;
  week: { date: string; minutes: number; lessons: number }[]; active_session: { id: string; started_at: string } | null };
export type GoogleStatus = { configured: boolean; connected: boolean; syncing: boolean; last_sync_at: string | null; error: string | null };
type AuthResult = { access_token: string; user: User };
type Options = RequestInit & { json?: unknown };
type AuthStore = { user: User | null; token: string | null; ready: boolean; error: string;
  bootstrap: () => Promise<void>; login: (email: string, password: string, name?: string, invite_code?: string) => Promise<void>; logout: () => Promise<void> };
type SessionScope = { userId: string | null; epoch: number };

let sessionEpoch = 0;
let cookieOperations: Promise<void> = Promise.resolve();
let refreshing: Promise<void> | null = null;

export class SessionChangedError extends Error {
  constructor() {
    super("Your account changed. Please retry this action.");
    this.name = "SessionChangedError";
  }
}

function sessionScope(): SessionScope {
  return { userId: useAuth.getState().user?.id ?? null, epoch: sessionEpoch };
}

function assertScope(scope: SessionScope) {
  if (scope.epoch !== sessionEpoch || scope.userId !== (useAuth.getState().user?.id ?? null)) {
    throw new SessionChangedError();
  }
}

// Always serialize within this tab; Web Locks add cross-tab serialization where supported.
function cookieLock(run: () => Promise<void>): Promise<void> {
  const operation = cookieOperations.then(async () => {
    if (typeof navigator !== "undefined" && navigator.locks) await navigator.locks.request("synapse-refresh", run);
    else await run();
  });
  cookieOperations = operation.catch(() => {});
  return operation;
}

export const useAuth = create<AuthStore>((set) => ({
  user: null, token: null, ready: false, error: "",
  bootstrap: async () => {
    try { await refresh(); }
    catch (error) { set({ error: error instanceof Error && !error.message.includes("Session expired") ? error.message : "" }); }
    finally { set({ ready: true }); }
  },
  login: async (email, password, name, invite_code) => {
    await cookieLock(async () => {
      const result = await api<AuthResult>(name ? "/auth/register" : "/auth/login", {
        method: "POST", json: name ? { email, password, name, invite_code } : { email, password }
      }, false);
      sessionEpoch += 1;
      set({ user: result.user, token: result.access_token, error: "" });
    });
  },
  logout: async () => {
    await cookieLock(async () => {
      await api("/auth/logout", { method: "POST" }, false);
      sessionEpoch += 1;
      set({ user: null, token: null });
    });
  }
}));

async function refresh(scope?: SessionScope) {
  if (!refreshing) {
    refreshing = cookieLock(async () => {
      // A sign-in/out may have completed while this refresh waited for the lock.
      if (scope) assertScope(scope);
      const before = sessionScope();
      const response = await fetch("/api/auth/refresh", {
        method: "POST", credentials: "same-origin", cache: "no-store"
      });
      assertScope(before);
      if (response.status === 401) {
        sessionEpoch += 1;
        useAuth.setState({ user: null, token: null });
        throw new Error("Session expired");
      }
      if (!response.ok) {
        throw new Error(response.status === 429
          ? `Session refresh limit reached. Retry in ${response.headers.get("Retry-After") || "60"} seconds.`
          : "Workspace unavailable. Check the API connection.");
      }
      const result: AuthResult = await response.json();
      assertScope(before);
      if (before.userId !== result.user.id) sessionEpoch += 1;
      useAuth.setState({ token: result.access_token, user: result.user });
    }).finally(() => { refreshing = null; });
  }
  return refreshing;
}

export async function api<T = unknown>(path: string, options: Options = {}, retry = true): Promise<T> {
  const scope = sessionScope();
  const { json, ...init } = options;
  const headers = new Headers(init.headers);
  const token = useAuth.getState().token;
  if (token) headers.set("Authorization", "Bearer " + token);
  if (json !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch("/api" + path, {
    ...init, headers, body: json !== undefined ? JSON.stringify(json) : init.body,
    credentials: "same-origin", cache: "no-store"
  });
  assertScope(scope);
  if (response.status === 401 && retry) {
    // A slower request may return 401 after a sibling already refreshed this session.
    if (token === useAuth.getState().token) await refresh(scope);
    assertScope(scope);
    return api<T>(path, options, false);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    assertScope(scope);
    const detail = typeof body?.detail === "string" ? body.detail : response.status === 422
      ? "Check the form fields and try again." : "Request failed. Please try again.";
    throw new Error(detail + (response.status === 429 ? ` Retry in ${response.headers.get("Retry-After") || "60"} seconds.` : ""));
  }
  const result: T = response.status === 204 ? undefined as T : await response.json();
  assertScope(scope);
  return result;
}

export function message(error: unknown) { return error instanceof Error ? error.message : "Something went wrong. Please retry."; }
export function clock(minute: number) { return `${Math.floor(minute / 60).toString().padStart(2, "0")}:${(minute % 60).toString().padStart(2, "0")}`; }
export function campusTime(now: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: timezone, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(now);
  const get = (type: string) => parts.find(p => p.type === type)?.value || "0";
  return { day: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(get("weekday")), minute: Number(get("hour")) * 60 + Number(get("minute")) };
}
