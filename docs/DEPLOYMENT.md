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
8. **Tweet extraction (optional).** With no `LLM_PROVIDER` set, the worker runs exactly as
   above and logs `extraction disabled` once; `python -m app.worker status` shows
   `Extraction: disabled`. To turn it on, set the worker service's variables (placeholders
   only here; the values go in Railway's variables, never in the repository or in logs):
   - `LLM_PROVIDER` — one of `google`, `openai`, `anthropic`, `openrouter`.
   - `LLM_MODEL` — the model name for that provider (required until ADR 0006 names a
     default).
   - the key of the chosen provider: `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
     or `OPENROUTER_API_KEY`. An unknown provider or a missing key fails the worker on start
     with a message naming the variable, never its value.
   - `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` from a Langfuse Cloud project in the EU
     region, and `LANGFUSE_HOST` (defaults to `https://cloud.langfuse.com`, the EU region).
     Without both keys extraction still runs, untraced, and the worker logs one warning.
   - `USD_PLN_RATE` — only the evaluation command needs it; the worker does not.
   `python -m app.worker status` then prints `Extraction:` with the model, the posts waiting,
   the failed posts and the latest extraction. New posts are extracted oldest first within
   seconds of being stored, in their own thread, so a slow provider never delays tweet polls
   or FPL jobs; a failed post is stored as `failed` and never blocks the rest. Re-extraction
   and evaluation run from a shell with the production variables, or locally:
   `python -m app.extraction reextract --failed`, `python -m app.extraction prelabel`,
   `python -m app.extraction evaluate` (see the README's Development section).
