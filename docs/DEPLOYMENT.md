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
