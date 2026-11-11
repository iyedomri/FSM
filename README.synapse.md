# Project Synapse — FSM Club academic OS

A working foundation: Next.js/TypeScript/Tailwind/Zustand/Framer Motion, FastAPI,
PostgreSQL, Redis, LangChain/OpenAI and a dedicated Pinecone serverless index.
No seeded users, fake AI responses, or fabricated official campus coordinates.

## Bootstrap and local deployment

Prerequisites: Bash, Python 3, Docker Engine with Compose v2. Local Node/npm is optional;
use Node 22.12+ (22.x), 24.x or 26+ for development and verification. The initializer
can generate the frontend lockfile through a Node 22 container instead.
The initializer refuses to overwrite an existing scaffold and preserves `.env`.

1. Run `bash init_synapse.sh ./synapse` (or `.` inside an empty repository).
2. Enter that directory. `.env` contains generated **development-only** secrets.
   Share `REGISTRATION_INVITE` privately with approved FSM members, never in client code.
3. Set `OPENAI_API_KEY` and `PINECONE_API_KEY`. To provision an index:
   `docker compose run --rm --no-deps migrate python -m ai.provision`.
   Copy the printed `PINECONE_INDEX_HOST` into `.env`.
   The index must use cosine distance and 1536 dimensions. Provisioning can incur charges.
4. Run `docker compose up --build -d --wait --wait-timeout 300`.
5. Visit `http://localhost:3000`, register with your invitation, add timetable slots,
   and upload a **text-based PDF**, not a scan. A configured provider is required for AI.
   Readiness: `/api/health/ready`. Logs: `docker compose logs -f backend worker`.

Without AI or Google keys, authentication, schedule and focus tracking still work;
AI returns an explicit configuration error and the Classroom control is disabled.
PostgreSQL and Redis have no published ports. Host localhost URLs in `.env` are for
optional direct Python development; Compose overrides them with internal service URLs.
For a hosted preview, set PUBLIC_ORIGIN to its exact HTTPS origin and SECURE_COOKIES=true.
Browser API requests always use relative URLs; secrets never enter the frontend bundle.

## Google Classroom

Enable Classroom and Drive APIs in a Google Cloud project. Create a Web OAuth client;
configure the consent screen/test users and the redirect URI **exactly** as
`PUBLIC_ORIGIN/api/integrations/google/callback`. Set GOOGLE_CLIENT_ID and
GOOGLE_CLIENT_SECRET, restart backend/worker, then select Connect Classroom.
The app requests read-only courses, courseWorkMaterials, and Drive access to download
existing Classroom attachments. Google Workspace administrators may restrict consent;
production external apps may require Google's verification. No Google app is connected
merely by running this scaffold.

OAuth uses single-use Redis state (hashed keys, 10-minute TTL), a browser-bound
HttpOnly/SameSite=Lax cookie, and S256 PKCE. Production uses a Secure `__Host-` state
cookie with Path=/ and no Domain, preventing sibling subdomains from planting it.
Successful registration/sign-in, logout and Classroom disconnection clear pending
browser consent; start a fresh flow after changing accounts. Consent denial consumes
state too. Malformed, incomplete, non-bearer or partially scoped token responses fail
closed without replacing an existing grant. State-store outages return retryable 503s.
Tokens are encrypted with AES-256-GCM, a fresh nonce, and user-bound associated data;
parallel first-time callbacks use an atomic PostgreSQL upsert. After upgrading from
an earlier state-cookie format, restart any pending Google consent flow.
The independent worker uses PostgreSQL row leases and polls every five minutes,
processing at most three PDFs per pass with bounded pagination. It imports new/updated
**course material PDF attachments**, not Google Docs exports, assignments, or OCR.
It shares the user's 10-AI-requests/hour budget. Status/errors appear in the dashboard.
Disconnect deletes stored credentials, not generated lessons. Already-accepted callbacks
and running jobs can finish; revoke the app in Google account settings to withdraw
Google's grant as well.

## Production deployment

Set ENVIRONMENT=production, SECURE_COOKIES=true, PUBLIC_ORIGIN=https://your-domain,
and SITE_HOST=your-domain. Point DNS at the host, open TCP 80/443 (UDP 443 optional),
and run `./ops/deploy.sh`. Caddy supplies HTTPS; HTTP on 3000 remains loopback-only.
Never expose PostgreSQL, Redis or the API directly. Production authentication requires
Caddy to overwrite X-Synapse-Client-IP; do not expose the frontend directly either. Protect `.env` (mode 600), rotate
credentials in a secret manager, pin container digests in your release process,
back up encrypted PostgreSQL/Redis volumes, and rehearse restores before real data.
Do not run `docker compose down -v` unless deliberately deleting persistent data.
For rolling production migrations, run the migration job explicitly before replacing
API replicas; the compose stack itself is a single-host foundation, not an HA cluster.

Access JWTs use HS256 with issuer/audience validation and a fixed 15-minute lifetime.
Refresh tokens are opaque, SHA-256 hashed in PostgreSQL, rotated atomically, expire
in 30 days, and are stored only in HttpOnly/Secure/SameSite cookies in production.
Replay revokes the refresh family. Logout does not revoke an already issued access
JWT; it naturally expires within 15 minutes. A per-tab queue serializes sign-in,
registration, refresh and logout; browser Web Locks extend serialization across tabs
where supported. Protected requests bind their results and their single retry to the
initiating account and local sign-in session, including after asynchronous JSON decoding.
An account change discovered during refresh never replays an old mutation as the new
account. Account changes remount the workspace so lessons/timers from the previous user
cannot remain visible. Only a rejected refresh (401) clears the local session; transient
connection/server failures surface a retryable error instead. Every mutating API request
must include the configured Origin, including curl clients. Access tokens stay in memory,
never localStorage.
Registration is invitation-gated, not institution-verified SSO; use a real campus
identity provider/verified membership system before treating an email as proof of enrollment.

## Data and AI boundaries

Raw PDF bodies are streamed into a bounded bytearray, not Starlette UploadFile (which
spools to disk). An isolated, memory/CPU/time-limited subprocess parses BytesIO; scans,
encrypted files, over-80-page files and over-budget text are rejected. Buffers are
released/overwritten after extraction; Python/OS copies cannot promise forensic RAM
erasure. Disable host swap/core dumps and request-body capture in upstream tooling.
PDF bytes are never written to disk or queued. Derived summaries/quizzes are retained
in PostgreSQL and text chunks/embeddings in user-isolated Pinecone namespaces until
the lesson is deleted. This is **not** a claim that OpenAI/Pinecone retain no data;
review provider retention, residency, contracts and institutional consent separately.
LangSmith tracing is off by default. Never enable raw request/response logging.

LangChain chunks with overlap, applies an 18,000-source-token budget, generates a
validated JSON schema (exactly 5 MCQs / 3 flashcards) and checks citation IDs. RAG is
scoped to both the authenticated user and an owned document. Prompts treat course
text as untrusted; prompt injection and hallucinations cannot be perfectly eliminated.
Always verify scientific formulas against originals. API outputs render as Markdown
without raw HTML. Baseline CSP permits inline Next.js hydration; adopt a nonce-based
CSP before stricter institutional deployments. Configure provider-level spend caps.

The campus SVG/room names are explicitly **illustrative**, not an official FSM map.
Replace its room registry and backend ROOMS allowlist with surveyed, approved data.
Current class highlighting derives from the personal weekly timetable and Africa/Tunis
time, not GPS or live faculty occupancy. Focus time is server-timed, capped at two
hours/session. Passing a quiz awards completion XP once; this is not an exam system.

## Verification

Run `./ops/verify.sh` for installer-drift checks, lint, Python tests, frontend Vitest
unit tests, typecheck and production build. The frontend suite covers account/session
races, stale mutation retries, cookie-operation serialization (including the no-Web-Locks
fallback), concurrent/late 401s, quota messaging and Tunis timetable rollover. These are
Node unit tests, not a substitute for browser/Google end-to-end tests.
Set `REQUIRE_INTEGRATION=1` plus `TEST_DATABASE_URL` to insist on
real PostgreSQL tests. Only a disposable database named `synapse_test` is accepted;
the integration fixture resets its public schema and applies the actual Alembic migrations.
The suite covers migration/model agreement, refresh replay and concurrency, document
ownership, server-side grading/one-time XP, shared AI quotas, safe deletion, RAM buffer
cleanup and Google reconnect races. Google HTTP-contract tests additionally exercise
browser/user state binding, PKCE, cookie policy, consent denial/replay, malformed or
partial grants, encrypted persistence, state-store outages and parallel initial callbacks.
External AI/Google provider responses use explicit mocks/HTTP MockTransport in those
tests, not live-provider verification. Test configuration always disables real provider keys.

After editing source, run `python3 ops/sync_initializer.py --write`. It updates the
self-contained installer without embedding `.env`, dependencies, build output or the
generated Next.js type imports. CI checks for drift. Generated projects retain a copy
of their initializer when it was executed from a file.

Failed vector deletions use `cleanup_pending`; the worker retires successful cleanup
records instead of repeatedly processing the same first batch. An old token refresh
cannot overwrite a newer Google grant after reconnect. Existing deployments should run
`docker compose run --rm migrate` before restarting API/worker to apply revision 0002.

Frontend: `cd frontend && npm ci --ignore-scripts && npm test && npm run typecheck && npm run build`.
Backend: create a virtualenv, install `backend/requirements-dev.txt`, then run
`cd backend && pytest && ruff check .`. Unit tests require no API keys or services;
quota tests execute Redis Lua through fakeredis, PDF tests use in-memory fixtures.
The optional integration test resets **only a disposable database named synapse_test**:
`TEST_DATABASE_URL=postgresql+asyncpg://.../synapse_test pytest`.
CI runs it against PostgreSQL. Docker/real-provider/Google end-to-end verification
still requires a deployment with those services and authorized credentials.
Dependencies are pinned; review vulnerabilities and regenerate locks regularly.
