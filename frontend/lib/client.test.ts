import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Client = typeof import("./client");
let client: Client;
const alice = { id: "alice", name: "Alice", email: "alice@example.edu" };
const bob = { id: "bob", name: "Bob", email: "bob@example.edu" };
const json = (value: unknown, status = 200, headers?: HeadersInit) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", ...headers } });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

function browserLocks() {
  let queue = Promise.resolve();
  return { request: vi.fn((_name: string, callback: () => Promise<void>) => {
    const run = queue.then(callback);
    queue = run.catch(() => {});
    return run;
  }) };
}

beforeEach(async () => {
  vi.resetModules();
  vi.stubGlobal("navigator", { locks: browserLocks() });
  client = await import("./client");
  client.useAuth.setState({ user: alice, token: "old-alice-token", ready: true, error: "" });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("account-scoped API requests", () => {
  it("never returns a late response from a previous account", async () => {
    const response = deferred<Response>();
    const fetch = vi.fn().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetch);
    const pending = client.api("/documents");
    const rejected = expect(pending).rejects.toThrow("Your account changed");
    client.useAuth.setState({ user: bob, token: "bob-token" });
    response.resolve(json([{ title: "Alice private notes" }]));
    await rejected;
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not replay an expired mutation as the next account", async () => {
    const response = deferred<Response>();
    const fetch = vi.fn().mockReturnValueOnce(response.promise)
      .mockResolvedValueOnce(json({ access_token: "bob-token", user: bob }))
      .mockResolvedValue(json({ id: "would-be-bob-focus-session" }));
    vi.stubGlobal("fetch", fetch);
    const pending = client.api("/study/start", { method: "POST" });
    const rejected = expect(pending).rejects.toThrow("Your account changed");
    client.useAuth.setState({ user: bob, token: "bob-token" });
    response.resolve(json({ detail: "Expired" }, 401));
    await rejected;
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("discards in-flight results even when the same account signs in again", async () => {
    const response = deferred<Response>();
    const fetch = vi.fn((url: string) => url === "/api/auth/login"
      ? Promise.resolve(json({ access_token: "new-login-token", user: alice }))
      : response.promise);
    vi.stubGlobal("fetch", fetch);
    const pending = client.api("/documents");
    const rejected = expect(pending).rejects.toThrow("Your account changed");
    await client.useAuth.getState().login(alice.email, "test-password");
    response.resolve(json([{ title: "Previous login result" }]));
    await rejected;
    expect(client.useAuth.getState().token).toBe("new-login-token");
  });

  it("does not resurrect an account when an old 401 arrives after logout", async () => {
    const response = deferred<Response>();
    const fetch = vi.fn((url: string) => url === "/api/auth/logout"
      ? Promise.resolve(new Response(null, { status: 204 }))
      : response.promise);
    vi.stubGlobal("fetch", fetch);
    const pending = client.api("/dashboard");
    const rejected = expect(pending).rejects.toThrow("Your account changed");
    await client.useAuth.getState().logout();
    response.resolve(json({ detail: "Expired" }, 401));
    await rejected;
    expect(client.useAuth.getState().user).toBeNull();
    expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/dashboard", "/api/auth/logout"]);
  });

  it("rejects an account change that occurs while JSON is being decoded", async () => {
    const body = deferred<unknown>();
    const started = deferred<void>();
    const response = json({});
    vi.spyOn(response, "json").mockImplementation(() => { started.resolve(); return body.promise; });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const pending = client.api("/documents");
    const rejected = expect(pending).rejects.toThrow("Your account changed");
    await started.promise;
    client.useAuth.setState({ user: bob, token: "bob-token" });
    body.resolve([{ title: "Alice private notes" }]);
    await rejected;
  });
});

describe("cookie rotation", () => {
  it("does not replay a mutation when another tab changed the refresh-cookie account", async () => {
    const fetch = vi.fn((url: string) => Promise.resolve(url === "/api/auth/refresh"
      ? json({ access_token: "bob-token", user: bob })
      : json({ detail: "Expired" }, 401)));
    vi.stubGlobal("fetch", fetch);
    await expect(client.api("/study/start", { method: "POST" })).rejects.toThrow("Your account changed");
    expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/study/start", "/api/auth/refresh"]);
    expect(client.useAuth.getState().user).toEqual(bob);
  });

  it("does not overwrite a newer account while the refresh body is being decoded", async () => {
    const body = deferred<unknown>();
    const started = deferred<void>();
    const response = json({});
    vi.spyOn(response, "json").mockImplementation(() => { started.resolve(); return body.promise; });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const restoring = client.useAuth.getState().bootstrap();
    await started.promise;
    client.useAuth.setState({ user: bob, token: "bob-token" });
    body.resolve({ access_token: "stale-alice-token", user: alice });
    await restoring;
    expect(client.useAuth.getState().user).toEqual(bob);
    expect(client.useAuth.getState().token).toBe("bob-token");
  });

  it("checks the initiating session again when a refresh waits behind sign-in", async () => {
    const loginResponse = deferred<Response>();
    const fetch = vi.fn((url: string) => url === "/api/auth/login" ? loginResponse.promise
      : Promise.resolve(json({ detail: "Expired" }, 401)));
    vi.stubGlobal("fetch", fetch);
    const signingIn = client.useAuth.getState().login(bob.email, "test-password");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const pending = client.api("/study/start", { method: "POST" });
    const rejected = expect(pending).rejects.toThrow("Your account changed");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    loginResponse.resolve(json({ access_token: "bob-token", user: bob }));
    await signingIn;
    await rejected;
    expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/auth/login", "/api/study/start"]);
    expect(client.useAuth.getState().user).toEqual(bob);
  });

  it("shares one refresh for concurrent 401s and retries with the new access token", async () => {
    const refreshed = deferred<Response>();
    const fetch = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/auth/refresh") return refreshed.promise;
      return Promise.resolve(new Headers(init?.headers).get("Authorization") === "Bearer new-alice-token"
        ? json({ ok: true }) : json({ detail: "Expired" }, 401));
    });
    vi.stubGlobal("fetch", fetch);
    const first = client.api("/dashboard");
    const second = client.api("/schedule");
    await vi.waitFor(() => expect(fetch.mock.calls.filter(call => call[0] === "/api/auth/refresh")).toHaveLength(1));
    refreshed.resolve(json({ access_token: "new-alice-token", user: alice }));
    expect(await Promise.all([first, second])).toEqual([{ ok: true }, { ok: true }]);
    expect(fetch).toHaveBeenCalledTimes(5);
  });

  it("serializes sign-in behind an in-flight refresh using the same browser lock", async () => {
    const refreshed = deferred<Response>();
    const fetch = vi.fn((url: string) => url === "/api/auth/refresh" ? refreshed.promise
      : Promise.resolve(json({ access_token: "bob-token", user: bob })));
    vi.stubGlobal("fetch", fetch);
    const restoring = client.useAuth.getState().bootstrap();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const signingIn = client.useAuth.getState().login(bob.email, "test-password");
    await Promise.resolve();
    expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/auth/refresh"]);
    refreshed.resolve(json({ access_token: "restored-alice-token", user: alice }));
    await Promise.all([restoring, signingIn]);
    expect(client.useAuth.getState().user).toEqual(bob);
    expect(client.useAuth.getState().token).toBe("bob-token");
    expect(navigator.locks.request).toHaveBeenCalledTimes(2);
    expect(navigator.locks.request).toHaveBeenNthCalledWith(1, "synapse-refresh", expect.any(Function));
    expect(navigator.locks.request).toHaveBeenNthCalledWith(2, "synapse-refresh", expect.any(Function));
  });

  it("serializes cookie operations within this tab even without Web Locks", async () => {
    vi.stubGlobal("navigator", {});
    const refreshed = deferred<Response>();
    const fetch = vi.fn((url: string) => url === "/api/auth/refresh" ? refreshed.promise
      : Promise.resolve(json({ access_token: "bob-token", user: bob })));
    vi.stubGlobal("fetch", fetch);
    const restoring = client.useAuth.getState().bootstrap();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const signingIn = client.useAuth.getState().login(bob.email, "test-password");
    await Promise.resolve();
    expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/auth/refresh"]);
    refreshed.resolve(json({ access_token: "new-alice-token", user: alice }));
    await Promise.all([restoring, signingIn]);
    expect(client.useAuth.getState().user).toEqual(bob);
  });

  it("reuses a completed refresh when a slower request returns an old-token 401", async () => {
    const slowResponse = deferred<Response>();
    const fetch = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/auth/refresh") return Promise.resolve(json({ access_token: "new-alice-token", user: alice }));
      if (new Headers(init?.headers).get("Authorization") === "Bearer new-alice-token") return Promise.resolve(json({ ok: true }));
      return url === "/api/schedule" ? slowResponse.promise : Promise.resolve(json({ detail: "Expired" }, 401));
    });
    vi.stubGlobal("fetch", fetch);
    const first = client.api("/dashboard");
    const second = client.api("/schedule");
    await expect(first).resolves.toEqual({ ok: true });
    slowResponse.resolve(json({ detail: "Expired" }, 401));
    await expect(second).resolves.toEqual({ ok: true });
    expect(fetch.mock.calls.filter(call => call[0] === "/api/auth/refresh")).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(5);
  });

  it("a rejected sign-in does not poison the cookie queue or replace the current session", async () => {
    const fetch = vi.fn((url: string) => Promise.resolve(url === "/api/auth/login"
      ? json({ detail: "Invalid email or password" }, 401)
      : new Response(null, { status: 204 })));
    vi.stubGlobal("fetch", fetch);
    await expect(client.useAuth.getState().login(bob.email, "wrong-password")).rejects.toThrow("Invalid email or password");
    expect(client.useAuth.getState().user).toEqual(alice);
    await client.useAuth.getState().logout();
    expect(client.useAuth.getState().user).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("preserves the current session on a transient refresh failure without replaying an action", async () => {
    const fetch = vi.fn((url: string) => Promise.resolve(json({ detail: "Unavailable" }, url === "/api/auth/refresh" ? 503 : 401)));
    vi.stubGlobal("fetch", fetch);
    await expect(client.api("/study/start", { method: "POST" })).rejects.toThrow("Workspace unavailable");
    expect(client.useAuth.getState().user).toEqual(alice);
    expect(client.useAuth.getState().token).toBe("old-alice-token");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("does not infinitely refresh when the retry is still unauthorized", async () => {
    const fetch = vi.fn((url: string) => Promise.resolve(url === "/api/auth/refresh"
      ? json({ access_token: "new-alice-token", user: alice })
      : json({ detail: "Still unauthorized" }, 401)));
    vi.stubGlobal("fetch", fetch);
    await expect(client.api("/documents")).rejects.toThrow("Still unauthorized");
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("clears a failed session without retrying the protected action", async () => {
    const fetch = vi.fn().mockResolvedValue(json({ detail: "Expired" }, 401));
    vi.stubGlobal("fetch", fetch);
    await expect(client.api("/study/start", { method: "POST" })).rejects.toThrow("Session expired");
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(client.useAuth.getState().user).toBeNull();
    expect(client.useAuth.getState().token).toBeNull();
  });
});

it("keeps requests relative, adds JSON/bearer headers and surfaces quota retry timing", async () => {
  const fetch = vi.fn().mockResolvedValue(json({ detail: "Usage limit reached" }, 429, { "Retry-After": "120" }));
  vi.stubGlobal("fetch", fetch);
  await expect(client.api("/documents/lesson/ask", { method: "POST", json: { question: "Why?" } }))
    .rejects.toThrow("Retry in 120 seconds");
  const [url, init] = fetch.mock.calls[0];
  expect(url).toBe("/api/documents/lesson/ask");
  expect(new Headers(init.headers).get("Authorization")).toBe("Bearer old-alice-token");
  expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  expect(init.credentials).toBe("same-origin");
  expect(init.cache).toBe("no-store");
  expect(JSON.parse(init.body)).toEqual({ question: "Why?" });
});

it("calculates Tunis weekdays across the UTC midnight boundary", () => {
  expect(client.campusTime(new Date("2026-11-15T22:30:00Z"), "Africa/Tunis")).toEqual({ day: 6, minute: 1410 });
  expect(client.campusTime(new Date("2026-11-15T23:15:00Z"), "Africa/Tunis")).toEqual({ day: 0, minute: 15 });
  expect(client.clock(545)).toBe("09:05");
});
