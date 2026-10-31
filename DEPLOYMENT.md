# Deployment: Railway (API, web page and both scheduled jobs) + Supabase (database)

Nothing here has been run on Railway yet: that needs your account. The code, the Docker image definition and the tests are ready (the image was reproduced step by step in a clean copy, but not built with Docker, which was not running); this file is the list of manual steps.

| Railway service | What it runs | Database connection |
|---|---|---|
| `api` | the API and the web page (`/app`), the image's default command | only the two limited roles (`API_DATABASE_URL`, `API_ADMIN_DATABASE_URL`) |
| `daily-collect` | `research-agent job daily`, cron `30 5 * * *` | the owner connection (`DATABASE_URL`) |
| `weekly-newsletter` | `research-agent job weekly`, cron `30 14 * * 4` (Thursday 14:30 UTC = 20:00 IST) | the owner connection (`DATABASE_URL`) |

All three are built from the same repository and the same `Dockerfile`; only the start command and schedule differ. The owner connection is given to the two job services only, never to the public API service. Cron times are UTC (Railway's rule).

Railway's own docs mark `railway.json` config files as deprecated for new services, so the settings below are entered in the dashboard instead.

**Why Supabase's pooler:** the direct address (`db.<ref>.supabase.co`) is IPv6 only. Use the **Session pooler** string (host `aws-0-<region>.pooler.supabase.com`, port 5432, user `postgres.<ref>`) from Supabase's Connect page, so the connection works wherever IPv6 is not available.

## 1. Supabase: API role connection strings
Note the pooler host from the Session pooler string, then re-make the two role connection strings for it (this rotates their passwords and writes both into your local `.env`):
```
research-agent db roles --pooler-host aws-0-<region>.pooler.supabase.com
```
`API_DATABASE_URL` and `API_ADMIN_DATABASE_URL` in `.env` then point at the pooler (session mode, port 5432, users `curt_api_read.<ref>` and `curt_api_config.<ref>`). You paste these into the `api` service in step 3. Use `--pooler-port 6543` only for serverless hosts.

## 2. GitHub: a private repository
Railway deploys from GitHub. Commit the project (nothing is committed yet) and push to a **private** repository:
```
git add -A && git commit -m "Curt research agent"
gh repo create <name> --private --source . --push
```
`.env`, `data/` and `outputs/` are git-ignored, so no key or local database is pushed. `.github/workflows/ci.yml` then runs the tests on every push.

## 3. Railway: one project, three services
Create a project and add the repository three times (New > GitHub Repo). Railway builds the `Dockerfile` each time.

**Service `api`** (leave the start command empty: the image runs the API on Railway's `PORT`)
* Settings > Networking > Generate Domain. This is the public address of the web page and the API.
* Variables:

  | Variable | Value |
  |---|---|
  | `API_TOKENS` | one or more long random read tokens (`research-agent api token` makes one) |
  | `API_ADMIN_TOKENS` | the token(s) allowed to change newsletter settings |
  | `API_DATABASE_URL` | from `.env` after step 1 (read-only role) |
  | `API_ADMIN_DATABASE_URL` | from `.env` after step 1 (settings-only role) |
  | `API_CLIENT_IP_HEADER` | `X-Real-IP` (Railway says its edge sets it; see the check below) |

  Do not add `DATABASE_URL` or the API keys here: the API needs none of them.
* Keep it at **one replica**: the rate limiter counts in the memory of one process.

**Service `daily-collect`**
* Settings > Deploy: Custom Start Command `research-agent job daily`, Cron Schedule `30 5 * * *`. No public domain.
* Variables: `DATABASE_URL` (the **Session pooler** owner string), `CONTACT_EMAIL`, `NCBI_API_KEY`, `OPENALEX_API_KEY`. Optional: `JOB_OVERLAP_DAYS` (default 3).

**Service `weekly-newsletter`**
* Settings > Deploy: Custom Start Command `research-agent job weekly`, Cron Schedule `30 14 * * 4` (Thursday 14:30 UTC, 20:00 IST; the edition is built then and dated Friday, see section 5). No public domain.
* Variables: the same four as above, plus `SPRINGER_API_KEY` (optional), `LLM_API_KEY`, `LLM_MODEL` (Groq; without them the edition uses abstract extracts), optionally `LLM_BASE_URL` and `LLM_REASONING_EFFORT` (for example `low` for `openai/gpt-oss-20b`), and optionally `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` (with them the job also creates a Gmail **draft**; it never sends) and `NEWSLETTER_RECIPIENTS` (the draft's "To" addresses, comma separated). `JOB_FULLTEXT_LIMIT` (default 500) is optional.
* Railway's Shared Variables can hold the values the two job services have in common, so they are entered once.

**Region (important):** set every service's region (Settings > Deploy) to the one nearest Supabase, which for this project is in Stockholm: choose Railway's EU region (Amsterdam). A collection makes thousands of small database calls, and each one pays the round-trip time. Far away (about 200 ms a call) a run takes roughly 15 minutes; nearby (about 20 to 30 ms) it takes a couple of minutes.

Railway cron rules to know (from its docs): schedules are UTC and at least 5 minutes apart; the start time can drift by a few minutes; the command must exit when done (both jobs do); a run that is still going when the next is due makes Railway skip that one; Railway never stops a hung run itself, so the jobs stop themselves after 45 minutes (daily) and 150 minutes (weekly).

## 4. First checks
1. Open `https://<your-domain>/health` (shows `ok`) and `/ready` (shows `ready`, so the database role works).
2. Open `https://<your-domain>/app`, paste a read token and search. Saving settings needs an admin token.
3. **Client address check:** with a read token, open `/v1/whoami` (for example `curl -H "Authorization: Bearer <token>" https://<your-domain>/v1/whoami`). `client` must be *your* address and must differ from `connecting_address` (Railway's proxy). If `client` equals `connecting_address`, or shows an address that is not yours, the header setting is wrong: every visitor would then share one rate limit. Fix `API_CLIENT_IP_HEADER` (Railway's forum has reports of this header showing a CDN address in some routing paths), or empty it and rely on the tokens.
4. Run the daily job once: temporarily set its Cron Schedule a few minutes ahead, or redeploy it, and read the service logs (each step is logged with a time). Check `/app` > Search now shows papers. Then the weekly job the same way; the first build takes about 15 to 25 minutes (summaries are paced to Groq's rate limit). The edition appears in `/app` > Editions.
5. Put the schedules back and leave them.

## 5. Day to day

**Timing and the edition date (owner decision 2026-10-31).** The weekly job runs on Thursday at 14:30 UTC (20:00 IST), 9 hours after that day's collection, so Thursday's papers are in. The edition is dated for Friday (`newsletter.edition_weekday: 4` in the profile; Monday is 0): the date is the next Friday on or after the UTC day of the run. Only the date moves: the contents are everything stored up to the moment the job starts, so papers found on Friday morning go into the following Thursday's edition, and none is ever lost or sent twice. Running the job again on Friday gives the same date, and an edition that is already published is never rebuilt. Keep the weekly time clear of the daily one (they must not overlap) and between 00:00 and 18:29 UTC, so the UTC day is also the day in India.

* **Failure signals:** a failed job exits non-zero, so Railway marks the run failed; check the project's notification settings in Railway for email or chat alerts (I have not verified what Railway offers on your plan). Separately, the weekly job refuses to build if no successful collection finished in the last 12 hours (`status --max-age-hours 12`). The daily collection runs at 05:30 UTC and the weekly job at 14:30 UTC on Thursday, 9 hours later, so if Thursday's collection failed the weekly job fails too (Railway shows it as failed) instead of quietly leaving Thursday's papers out; fix the collection, run the daily job by hand, then run the weekly job again.
* **Times:** change the Cron Schedule in each service's settings. Run the daily job by hand with `JOB_OVERLAP_DAYS` raised to catch up after an outage longer than 3 days, then lower it again.
* **Re-running the weekly job** for the same date is safe: an unpublished edition is returned unchanged, a paper is never carried twice, and a published edition is never rebuilt. To replace an unpublished edition run `research-agent newsletter build --rebuild` locally.
* **Schema changes:** both jobs run `db upgrade` first, so a pushed migration is applied by the next run. Re-run `db roles` only if a new table is added to what the API reads.
* **Changing a token:** change it in the `api` service and redeploy. Re-running `db roles` rotates the database role passwords; update the `api` service afterwards.
* **Cost:** Railway bills by usage. Our load is small (an always-on API plus about 2 minutes a day and under an hour a week of job time); check Railway's pricing page for the current plan prices.

## 6. What is not verified yet
* The Docker build itself (Docker was not running here), a real Railway deployment, real cron runs, and Railway's `X-Real-IP` header: use step 4 to check them.
* The Supabase session pooler from Railway (the direct connection and the roles were checked against Supabase from this machine; the pooler strings are covered by tests only).
* Gmail drafts (no Google credentials yet).
