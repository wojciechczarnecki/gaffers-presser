# Deployment

The worker runs on Railway next to a PostgreSQL service. The owner performs every step
below; agents never touch production.

1. Create a Railway project with a **PostgreSQL service** from the `pgvector/pgvector:pg16`
   image, with a **volume** mounted at `/var/lib/postgresql/data` and the environment
   variables `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` set (the same image as
   local Compose, so no data migration is needed later for pgvector).
2. Add a **worker service** from this GitHub repository. Railway reads `railway.json` from
   the repository root: it builds `backend/Dockerfile`, starts the worker
   (`python -m app.worker run`), runs `alembic upgrade head` as the pre-deploy command
   before every deploy, and restarts the worker on failure (`restartPolicyType: ALWAYS`).
3. Set the worker service's environment variables: `DATABASE_URL` (a reference to the
   database service — any of the `postgresql://`, `postgres://` or `postgresql+psycopg://`
   schemes works) and `FPL_LEAGUE_IDS`. If the pre-deploy command cannot reach the
   database's private network address, set `DATABASE_URL` to the database's public URL for
   that step instead.
4. Turn on **auto-deploy from `main`** with **"Wait for CI"** enabled, so merging a pull
   request (the owner's gate) is the deploy, and red CI blocks it.
5. Reading logs and running commands in production: `railway logs` (or the dashboard) shows
   `job started` / `job finished` lines and, on a restart, the catch-up sequence below.
   `railway ssh` opens a shell in the worker container to run `python -m app.worker status`
   or a CLI job (`python -m app.fpl …`) by hand — a CLI job waits for a running worker job
   to finish (they share the schedule's advisory locks) rather than running concurrently
   with it.
6. **First-deploy catch-up:** the worker runs a reference sync, then the results sync and
   league sync for every gameweek that is already `finished` and `data_checked`, in
   gameweek order, then follows the normal hourly/15-minute reference schedule. A
   `deadline snapshot missed` log line for the latest already-passed gameweek is expected —
   no snapshot was taken for it before this deploy existed.
7. **Tweet ingest (optional).** With no `TWEET_SOURCE` set, the worker runs exactly as
   above; `python -m app.worker status` shows `Tweet ingest: disabled`. To turn it on, set
   the worker service's variables:
   - `TWEET_SOURCE` — one of `twscrape`, `twitterapi_io`, `x_api`; set `twscrape`, the
     recommended source ([ADR 0005](adr/0005-default-tweet-source-twscrape.md)).
   - `X_LIST_ID` — the watched X List's numeric ID.
   - per source: `twscrape` needs `TWSCRAPE_USERNAME` and `TWSCRAPE_COOKIES` (and, if the
     image's default temp location is unsuitable, `TWSCRAPE_ACCOUNTS_DB`); `twitterapi_io`
     needs `TWITTERAPI_IO_KEY`; `x_api` needs `X_API_BEARER_TOKEN`.
   - All credentials go in Railway's variables, never in the repository or in logs; an
     unknown source name or a missing credential fails the worker on start with a message
     naming the missing variable, never its value.
   `python -m app.worker status` then also prints the source, the last successful poll, the
   next poll and the current mode (`window` near a deadline, `sparse` otherwise).
   - Migration `0010` (spec 010) adds `tweet.embedded` and `tweet.quoted_x_id` (indexed),
     backfills `quoted_x_id` from each stored twscrape payload's `quotedTweet.id`, and adds the
     `list_membership` table. It runs through the pre-deploy like the others, leaves existing
     data unchanged and downgrades cleanly.
   - With `twscrape`, the worker fetches the List's members when it starts and then every
     6 hours (a failed fetch is retried after 30 minutes and never stops polling); until the
     first fetch succeeds every stored post counts as a list post. On the first start after
     this migration, run `python -m app.tweets members` from a Railway shell to fetch the
     membership at once: it prints `List members: <n>  snapshot: <UTC time>`. The other sources
     cannot list members and treat every post as a list post.
   - A fetched membership that drops more than 3 of the stored members at once is refused
     (twscrape ends paging silently, so a cut-off fetch looks like a shrunk List): the worker
     logs `list membership snapshot refused: list membership shrank from <n> to <m> members`,
     keeps the previous snapshot and retries after 30 minutes. After removing more than 3
     accounts from the List on purpose, run `python -m app.tweets members --force` once.
   - Classification follows the newest snapshot of any List. After changing `X_LIST_ID`, run
     `python -m app.tweets members` and check that it prints the new List's member count;
     until a fetch for the new List succeeds, posts are classified by the old List's members.
8. **Tweet extraction (optional).** With no `OPENROUTER_API_KEY` set, the worker runs exactly
   as above and logs `extraction disabled` once, whatever `LLM_MODEL` says;
   `python -m app.worker status` shows `Extraction: disabled`. OpenRouter is the only LLM
   provider, so the key is the switch. To turn extraction on, set the worker service's
   variables (placeholders only here; the values go in Railway's variables, never in the
   repository or in logs):
   - `OPENROUTER_API_KEY` — the switch and the only LLM credential.
   - `LLM_MODEL` — optional OpenRouter model ID; empty means the default from
     [ADR 0006](adr/0006-default-extraction-model-openrouter.md), `openai/gpt-6-luna`.
   - `LLM_FALLBACK_MODEL` — optional OpenRouter model ID answering when the primary fails
     (OpenRouter's model fallback); empty means the default fallback from ADR 0006, `google/gemini-3.1-flash-lite`.
     The fallback must share the primary's structured-output method and reasoning effort: an
     explicit `LLM_FALLBACK_MODEL` that does not fails the worker on start, and the default
     fallback is dropped (no fallback) under an `LLM_MODEL` it cannot answer for. A model
     outside `backend/app/llm/model_settings.toml` fails the worker on start with a
     message naming the variable, never a key; add a row there and in `backend/app/llm/prices.toml` first.
   - `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` from a Langfuse Cloud project in the EU
     region, and `LANGFUSE_HOST` (defaults to `https://cloud.langfuse.com`, the EU region).
     Without both keys extraction still runs, untraced, and the worker logs one warning.
   - `USD_PLN_RATE` — only the evaluation command needs it; the worker does not.
   `python -m app.worker status` then prints `Extraction:` with the model, the fallback, the
   posts waiting, the failed posts and the latest extraction. New posts are extracted oldest
   first within seconds of being stored, in their own thread, so a slow provider never delays
   tweet polls or FPL jobs; a failed post is stored as `failed` and never blocks the rest.
   Re-extraction and evaluation run from a shell with the production variables, or locally:
   `python -m app.extraction reextract --failed`, `python -m app.extraction prelabel`,
   `python -m app.extraction evaluate --split dev --model <OpenRouter model ID>`,
   `python -m app.extraction compare-labels`, `python -m app.extraction spend` (see the
   README's Development section).
9. **Retrieval indexing.** Indexing runs whenever `OPENROUTER_API_KEY` is set (the same key as
   extraction); without it the worker logs `retrieval indexing disabled` and every other loop
   runs as before.
   - `EMBEDDING_MODEL` is optional; empty means the default `openai/text-embedding-3-small`.
     A model with no row in `backend/app/llm/prices.toml`, or with a chat-model row (one that
     has an output price), fails the worker on start with a message naming the variable, never
     a key; add an input-only row first.
   - Migration `0005` enables the `vector` and `unaccent` extensions and adds the full-text
     column and `post_embedding`; it runs through the pre-deploy like the others, and the
     PostgreSQL image already ships both extensions.
   - Posts are embedded oldest first in their own thread, so an embedding outage never
     delays tweet polls, extraction or FPL jobs. After the first deploy the loop backfills
     the posts stored before it on its own; their latency is not recorded, because it would
     measure the deploy date rather than the indexing path. A post that keeps failing is
     retried every 10 minutes, behind every never-attempted post, until 15 attempts in total;
     after that it stays `failed` until an `index` run.
   - Re-indexing with another model, or retrying posts the loop gave up on, runs from a shell
     with the production variables: `python -m app.retrieval index [--model <OpenRouter model
     ID>]`.
     `python -m app.retrieval status` prints the embedded, missing and failed posts per model
     and the cost, and `python -m app.retrieval search "<query>" [--mode fulltext|vector|hybrid]`
     searches the stored posts (`--mode fulltext` needs no key).
10. **Corroboration.** `python -m app.corroboration <player>` computes, on demand, what the
    latest news about a player is and how well it is backed; it is not a worker loop and needs
    no new variable. Run it from a Railway shell (`railway ssh`) or locally with the production
    variables.
    - Migration `0006` adds `tweet.reposted_author_handle` and backfills it from each post's
      stored `raw` payload for the reposts already stored (twscrape, twitterapi.io and the X
      API shapes). It runs through the pre-deploy like the others. X API reposts stored before
      this migration keep an empty original author, so they count as their list account.
    - The judge that labels posts only retrieval finds uses `OPENROUTER_API_KEY`, `LLM_MODEL`
      and `LLM_FALLBACK_MODEL` (extraction's settings); without the key the command uses the
      extracted claims only and prints `Retrieval and judge: skipped`. Langfuse keys, when set,
      make each run one trace.
    - The judge's evaluation set is reviewed and run locally, never by the worker:
      `python -m app.corroboration.evaluation build-cases | review | evaluate`.
11. **Delivery (optional).** Alerts and the presser leave through one delivery interface; this
    step sets its channel. With no `DELIVERY_PROVIDER` set, delivery is disabled: the worker and
    every loop run exactly as before and sending returns `disabled`. The only channel is `resend`;
    any other value stops the worker and the CLI at start with a message naming the variable.
    - `resend` sends e-mail over the Resend HTTP API and requires `RESEND_API_KEY` and
      `DELIVERY_EMAIL_TO` (one recipient). `DELIVERY_EMAIL_FROM` is optional and defaults to
      `onboarding@resend.dev`. Without a verified domain Resend sends only from
      `onboarding@resend.dev` and only to the address of the Resend account owner; any other
      recipient gets HTTP 403, and the send is recorded as `failed`. The free plan allows 100
      e-mails a day and 3,000 a month and pauses sending at the limit, with no charge.
    - Migration `0007` adds the `delivery_log` table (a unique idempotency key and the full
      message of every send). It runs through the pre-deploy like the others.
    - Check the credentials before the first real alert: from a Railway shell (`railway ssh`)
      run `python -m app.delivery send-test` — it sends the test message under a fresh `test:`
      key, prints the outcome and the provider message ID, and exits non-zero when delivery is
      disabled or the send failed. `python -m app.delivery status` prints the channel and the
      last 10 log rows (time, kind, status, attempts, provider ID; no addresses or bodies).
    - `python -m app.worker status` ends with a `Delivery:` line: the channel (or `disabled`),
      the time and kind of the last successful send and the number of failed sends in the last
      24 hours.
12. **Alerts (optional).** Before each deadline the worker e-mails team news about the players
    that matter to the league: a digest at the first slot (20:00 Warsaw time the day before the deadline by default), news at the later slots (one hour before the deadline by default) and a breaking
    e-mail for every new post from the last slot to the deadline. Alerts run only when delivery
    (step 11), tweet ingest (step 7) and extraction (step 8) are all enabled; otherwise
    `python -m app.worker status` ends with `Alerts: disabled (<reason>)` and the worker runs as
    before. The player scope uses the league IDs from `FPL_LEAGUE_IDS`.
    - Variables (all optional; an invalid value stops the worker and the CLI at start with a
      message naming it): `ALERTS_ENABLED=false` turns alerts off explicitly;
      `ALERT_SLOTS` (default `D-1@20:00,60`: comma-separated slots, each either minutes before
      the deadline or `D-<n>@HH:MM`, a Warsaw wall-clock time `n` (1 to 7) days before the
      deadline's Warsaw date; the first slot is the digest, the others are news, and breaking
      e-mails run from the last slot to the deadline; wall-clock slots are listed from the
      earliest to the latest, minutes slots are strictly decreasing and come after wall-clock
      ones; a wall-clock slot that does not fall before the next slot or the deadline is skipped
      for that deadline with a warning in the log; when FPL moves a deadline after its digest
      went out, the digest is not sent again);
      `ALERT_TRENDING_MIN_ACCOUNTS` (default `3`); `ALERT_WIDELY_OWNED_PERCENT` (default `15`,
      0-100); `ALERT_MAX_LOOKBACK_DAYS` (default `7`, a positive integer: the alert window starts
      at the previous deadline but never more than this many days before the alert deadline).
    - `ALERT_SLOTS_MINUTES` was removed (replaced by `ALERT_SLOTS`): a worker or alerts CLI
      started with it set stops with a message, so delete it from the environment.
    - Tweet polling: fast polling (every 20 s) starts `max(90, last minutes slot + 10)` minutes
      before the deadline, that is 90 minutes with the default slots, and no longer depends on
      the first slot. For every slot that falls before that window the poller makes one extra
      poll 10 minutes before the slot, so the digest sees posts at most about 10 minutes old.
    - Migrations `0008` (adds `player.selected_by_percent`, refreshed by every reference sync)
      and `0009` (the alert log tables `alert` and `alert_post`) run through the pre-deploy like
      the others. Existing data is unchanged and both migrations downgrade cleanly.
    - Inspect from a Railway shell (`railway ssh`): `python -m app.alerts status` (the current
      alert deadline, the next slot, the last alert and the number of failed alerts),
      `python -m app.alerts latency [--gameweek N | --rehearsal]` (post to inbox latency, split
      into post to first fetch, fetch to extraction and extraction to accepted by the provider,
      for all posts and again per alert kind, so the breaking path is read apart from the digest),
      and `python -m app.alerts preview --at <Warsaw time> [--kind digest|news] [--html FILE]`
      (renders the alert the worker would send at that moment, and with `--html` also writes its
      HTML part to a file; sends and writes nothing to the database).
    - `python -m app.worker status` ends with an `Alerts:` line: the next slot (or the breaking
      window's end), the last alert and the failed alerts of the current deadline; its times are
      Warsaw time and marked `Warsaw`, while the other status lines are UTC.
    - **Rehearsal (optional, for testing).** `ALERT_REHEARSAL_DEADLINE=<Warsaw time>` is an optional
      variable for testing: it makes the worker treat that moment as one extra alert deadline: fast tweet polling, the digest, the
      news slots and breaking e-mails run for it and are sent for real, in any environment. It is
      not written to the gameweek table, so FPL jobs, snapshots and gameweek numbering ignore it,
      and its alerts never affect a real deadline (the alert log is keyed by deadline). A moment
      in the past does nothing; one whose alert span (from its first slot, or the start of fast
      polling if earlier, to the deadline) overlaps a real deadline's alert span stops the
      worker at start with a message naming the variable. With the default slots that needs
      about a day of distance from a real deadline; for a quick test set `ALERT_SLOTS=120,30`
      alongside it. Remove the variable after the test.
13. **Presser (optional).** After the league sync of the latest finished gameweek, the worker
    generates and e-mails one presser per league in `FPL_LEAGUE_IDS`: a Polish text in FPL
    slang built from the league's facts (the manager and the flop of the gameweek, the
    captains, the bench, transfers and chips, the table), with a "send to WhatsApp" button. It
    runs only for the latest finished gameweek, so a catch-up after downtime sends nothing for
    older ones, and a restart never sends a presser twice (the delivery key is
    `presser:<season>:gw<N>:league<id>`). The first-deploy catch-up (step 6) therefore sends the
    presser of the latest finished gameweek once when the presser is enabled at the first start.
    - It needs delivery (step 11) and `OPENROUTER_API_KEY`; with either missing the worker logs
      `presser disabled: <reason>` once and `python -m app.worker status` ends with
      `Presser: disabled (<reason>)`. When enabled, the line reads `Presser: model=<m>  last:
      GW<n> <status>  failed: <k>` and the worker logs `presser enabled: model=<m>`.
    - Variables (all optional; an invalid value stops the worker and the CLI at start with a
      message naming it, never its value): `PRESSER_ENABLED=false` turns it off explicitly;
      `PRESSER_MODEL` (default `openai/gpt-6-luna`; a model needs rows in
      `app/llm/model_settings.toml` and `prices.toml`); `PRESSER_NICKNAMES`, a JSON object of
      FPL entry ID to nickname (`{"123": "Bartas"}`), at most 30 characters each and unique
      ignoring case. Without a nickname a manager appears under the first word of the FPL name.
      Nicknames are set only in the environment, never in the repository.
    - A failed generation is retried in-call, then recorded as `failed` and not retried by the
      worker; send it by hand. A worker crash between the league sync and the presser loses
      that presser the same way.
    - Migration `0011` adds the `presser` table (a row per generation: the fact sheet, the text,
      the model, tokens, cost, latency and the status). It runs through the pre-deploy like the
      others and downgrades cleanly.
    - From a Railway shell (`railway ssh`), with `--league <id> --gameweek <n>`:
      `python -m app.presser facts` prints the fact sheet, `python -m app.presser preview`
      generates and prints without sending, `python -m app.presser send` sends by hand, also
      for a missed gameweek, with the same key (a second `send` reports `already_sent`), and
      `python -m app.presser status` (no options) says whether the presser is enabled and shows
      the latest presser per league.
    - The evaluation tooling is `python -m app.presser.evaluation
      build-cases|evaluate|review|judge-review|summary`. The comparison of the candidate models
      with the owner's ratings is BACKLOG #32.
