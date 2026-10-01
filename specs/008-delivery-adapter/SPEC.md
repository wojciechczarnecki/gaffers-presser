---
status: spec-ready
stage_history:
  - "spec-draft — 2026-10-01"
  - "spec-ready — 2026-10-01"
metrics:
  started_at: 2026-10-01T20:55
---

# SPEC 008 — Delivery adapter (e-mail first)

## Goal

Alerts (Stage 2) and the presser (Stage 3) need one way to put a message in front of the owner.
This spec delivers a channel-agnostic delivery interface with a Resend e-mail adapter and a
file adapter for development, plus a delivery log that makes every send idempotent and
observable. It works when `python -m app.delivery send-test` lands a message in the owner's
inbox from production, a repeated send with the same key sends nothing, and a later WhatsApp or
Messenger channel is a new adapter with no change to its callers.

## Context

- ADR 0004 picks e-mail to the owner as the MVP channel for both alerts and the presser, with
  delivery behind an interface so a WhatsApp adapter can follow (BACKLOG #3). The owner treats
  e-mail as a temporary, simplest channel; the target is WhatsApp or Messenger, so the interface
  must not assume e-mail.
- State of the official chat APIs, checked 2026-10-01: WhatsApp Cloud API group messaging needs
  an Official Business Account and caps groups at 8 members; Messenger's Send API only reaches
  people who wrote to a page. Neither is a near-term option for a hobby bot, so e-mail stays the
  MVP channel.
- DECISIONS (2026-09-26) already names `delivery` as one of the `app/` modules; no code for it
  exists yet.
- The closest existing pattern is the swappable tweet source: `app/tweets/sources/base.py`
  (a `Protocol` plus typed errors), `app/tweets/config.py` (a name → required variables table,
  `ConfigError` naming the variable, an empty name disables the feature) and
  `app/core/settings.py` (`pydantic-settings`, `SecretStr`). The `httpx` adapters
  (`twitterapi_io.py`, `x_api.py`) are tested against recorded payloads via `MockTransport`.
- A retry-with-back-off helper exists in `app/llm/retry.py`.
- The worker prints its state with `python -m app.worker status` (`app/worker/cli.py`).
- The consumers (alert e-mails, the presser) come in later specs; spec 007's open question on
  post → inbox latency is answered by them, using the provider-accepted time this log records.
- Resend free plan: 100 e-mails a day, 3,000 a month, sending pauses at the limit with no
  charge. Without a verified domain the sender is `onboarding@resend.dev` and Resend accepts
  only the account owner's own address as the recipient (403 otherwise).

## Read context

- `docs/ROADMAP.md` — read in full. Stage 2 item 3, "E-mail delivery adapter behind an
  interface (ADR 0004), shared with the Stage 3 presser"; it precedes the alert e-mails (item 4).
- `docs/PROJECT.md` — read in full. FR-2.3 (alerts by e-mail with cited links), FR-3.2 (presser
  by e-mail, ready to paste into WhatsApp), NFR swappability (the e-mail provider is an adapter,
  switched by configuration), budget (e-mail inside 20 PLN a month), privacy (no e-mail
  addresses or credentials in logs, code, tests).
- `docs/DECISIONS.md` — read in full. ADR 0004 row; the module list naming `delivery`; the
  module-per-business-area layout; testcontainers for database tests; "a refactor a feature
  needs is done in that feature's spec" (2026-09-30).
- `docs/BACKLOG.md` — read in full. #3 (WhatsApp via Baileys) is the next channel; #11 is a
  precondition of the alerts spec, not of this one.
- `docs/CONVENTIONS.md` — read in full. Product content in `app/content/`; external APIs tested
  against recorded payloads; logs never carry e-mail addresses or credentials; exact pins.
- `docs/DEPLOYMENT.md` — read in full. Optional features are switched on by variables with
  start-up validation naming the variable; this spec adds a section in the same style.
- `docs/adr/0004-email-as-mvp-channel.md` — read in full. The decision this spec implements.
- `docs/adr/0003-swappable-tweet-source.md` — searched for "interface", "configuration"; the
  pattern this spec mirrors.

## Scope

- A `delivery` module with a channel-agnostic message (title, plain-text body, optional HTML
  body) and a channel interface; callers send through one service and never see the provider.
- A Resend adapter over its HTTP API (`httpx`, no new dependency).
- A file adapter that writes each message to a directory as a `.eml` file instead of sending.
- Configuration by environment variables with start-up validation; an empty provider disables
  delivery.
- A delivery log table with an idempotency key and the full message, and an Alembic migration.
- Short retries on transient provider failures.
- A CLI: `send-test` and `status`; a `Delivery:` line in `python -m app.worker status`.
- The test message's text in `app/content/`.
- `.env.example`, `docs/DEPLOYMENT.md` and `docs/DECISIONS.md` updated.

## Out of scope

- Alert and presser content, templates and scheduling — their own specs (Stage 2 item 4,
  Stage 3).
- Gmail SMTP and other e-mail providers — another adapter when needed.
- WhatsApp and Messenger adapters — BACKLOG #3.
- Several recipients, subscribers and a verified sending domain — with multi-tenancy (after the
  MVP).
- Resend delivery webhooks (delivered, bounced, opened) — the worker exposes no HTTP endpoint.
  New backlog item, P3, trigger: alerts reported as sent never reach the inbox.
- A worker loop that re-sends failed messages over hours — an alert is worthless minutes after
  a deadline; a caller may retry the same key itself.
- Attachments and images — BACKLOG #5.

## Requirements and acceptance criteria

Message and interface

- [ ] AC1: A message has a title, a plain-text body and an optional HTML body; a channel adapter
  receives exactly this and returns the provider's message ID (or `None` when the channel has
  none). Nothing in the interface is e-mail specific: the recipient and the sender come from the
  adapter's configuration, not from the caller.
- [ ] AC2: A message with an empty title or an empty plain-text body is rejected with a
  validation error before any provider call and before any log row is written.

Configuration

- [ ] AC3: `DELIVERY_PROVIDER` selects the adapter: `resend` or `file`; empty or unset disables
  delivery. Any other value fails start-up (worker and CLI) with a message naming the variable
  and the allowed values.
- [ ] AC4: `resend` requires `RESEND_API_KEY` and `DELIVERY_EMAIL_TO`; `DELIVERY_EMAIL_FROM` is
  optional and defaults to `onboarding@resend.dev`. A missing required variable fails start-up
  with a message naming the variable, never its value.
- [ ] AC5: `file` writes to `DELIVERY_FILE_DIR`, default `./outbox` relative to the working
  directory, created if missing.
- [ ] AC6: With delivery disabled, sending through the service returns a `disabled` outcome,
  writes no log row and logs one line per process saying delivery is disabled; the worker and
  every other loop run exactly as before.

Sending and the log

- [ ] AC7: Every send carries a caller-given idempotency key (e.g. `alert:gw7:fpl123:t-30`,
  `presser:gw7:league-a`) and a kind (`alert`, `presser`, `test`).
- [ ] AC8: The first send with a key writes one log row with the key, kind, channel, title,
  plain-text body, HTML body, status, provider message ID, number of attempts, the time the send
  was requested and the time the provider accepted it.
- [ ] AC9: A send with a key whose row is `sent` makes no provider call, writes nothing and
  returns the existing row's outcome (`already_sent` with the original provider ID and times).
- [ ] AC10: A send with a key whose row is `failed` tries again and updates that row (status,
  attempts, provider ID, times, error class); the key is never duplicated. Two concurrent sends
  with one key make at most one provider call.
- [ ] AC11: A timeout, a connection error, HTTP 5xx or HTTP 429 is retried up to 3 attempts in
  total with back-off (honouring `Retry-After` on 429, capped at 10 s per wait); any other 4xx
  is not retried. After the last failure the row is `failed` with the error class and HTTP
  status, and the caller gets a `failed` outcome rather than an exception.
- [ ] AC12: The Resend adapter sends one `POST /emails` with the API key as a bearer token, the
  configured from and to addresses, the title as the subject, the text body and, when present,
  the HTML body; it reads the message ID from the response. Tested against recorded responses:
  200, 403 (testing-domain recipient), 422, 429 with `Retry-After`, 500 and a timeout.
- [ ] AC13: The file adapter writes one RFC 5322 `.eml` file per message, multipart with a text
  part and, when present, an HTML part, named so files sort by send time, and returns no
  provider ID; the file opens in a mail client and its text part equals the message's text
  body.
- [ ] AC14: Application logs for a send carry only the key's kind, the log row ID, the channel,
  the status, the attempts and the error class — never the recipient, the sender, the title,
  the body or the API key. A test captures the logs of a successful and a failed send and finds
  none of those values.

CLI and status

- [ ] AC15: `python -m app.delivery send-test` sends the test message (title and body from
  `app/content/`, with the current Warsaw time) under a fresh `test:` key and prints the outcome
  and the provider ID; with delivery disabled it says so and exits non-zero.
- [ ] AC16: `python -m app.delivery status` prints the configured channel (or `disabled`) and the
  last 10 log rows: time, kind, status, attempts, provider ID — no addresses or bodies.
 
- [ ] AC17: `python -m app.worker status` prints a `Delivery:` line with the channel (or
  `disabled`), the last successful send's time and kind and the number of `failed` rows in the
  last 24 hours.

Data

- [ ] AC18: An Alembic migration adds the delivery log table with a unique idempotency key;
  `alembic upgrade head` and `downgrade -1` run on the test container.
- [ ] AC19: Tests use synthetic addresses and keys only; no real recipient or key appears in the
  repository.

Documentation

- [ ] AC20: `.env.example` lists the new variables with empty values and comments;
  `docs/DEPLOYMENT.md` gains a "Delivery" step (variables, the Resend testing-domain limit, how
  to run `send-test` from `railway ssh`); `docs/DECISIONS.md` gains any rows from the decisions
  below not already recorded with this SPEC; `docs/BACKLOG.md` gains the webhooks item.

## Decisions and rejected alternatives

| Decision | Rejected alternatives | Rationale |
|----------|-----------------------|-----------|
| E-mail through the Resend HTTP API is the first channel | Gmail SMTP; the official WhatsApp Cloud API now; Baileys now | free tier covers the volume, `httpx` is already pinned, recorded-payload tests like the tweet sources; Gmail to oneself often gives no push notification; WhatsApp groups need an Official Business Account (the owner) |
| A channel-agnostic interface (title, text, optional HTML; recipient in the adapter's configuration) | an e-mail-shaped interface (to, from, subject) | e-mail is temporary; WhatsApp or Messenger must be a new adapter only (the owner) |
| A delivery log with a unique idempotency key and the full message | a stateless adapter with deduplication left to each caller | the worker's catch-up after a restart could send a presser or an alert twice; the log gives alerts the provider-accepted time for latency and keeps sent pressers as history (the owner) |
| Short in-call retries on transient errors, then `failed` | a re-send loop in the worker; no retries | an alert loses its value within minutes; a 4xx will not fix itself (the owner) |
| A `file` adapter writing `.eml` files | a fake only inside `pytest` | messages can be inspected locally and in agent sessions with no key and no send (the owner) |
| One recipient, `DELIVERY_EMAIL_TO` | a comma-separated list | ADR 0004 delivers to the owner; Resend without a domain sends only to the account owner (the owner) |
| The full message is stored in the database; logs carry none of it | metadata only | the database already holds manager names; the presser history is reusable; logs stay clean (the owner) |
| `send-test` and `status` CLI plus a worker status line | `send-test` only | the owner verifies production credentials before the first real alert (the owner) |

## Owner decisions

- Data migration (a new delivery log table, run by the Railway pre-deploy like the others) —
  accepted.
- New dependency — none needed (`httpx` and the standard library `email` package).
- The tweet configuration tests that read process environment variables (they fail in cloud
  sessions where `TWEET_SOURCE` / `TWSCRAPE_*` are set) are fixed in a separate fast-path PR
  before this spec ships, not here.

## Open questions (non-blocking)

- Whether `app/llm/retry.py` moves to `app/core` for reuse here or the delivery retry stays its
  own — the plan decides (DECISIONS 2026-09-30: the refactor goes in this spec if needed).
