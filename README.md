# migraine-research-agent

Daily collection pipeline and database for research monitoring. Field-specific behaviour (what counts as relevant, which journals,
researchers and subjects to watch) lives in `config/`, not in code. The first profile is **Curt migraine** (10 journals, 30 researchers,
12 subjects). Design and evidence are in `Pipeline_design_plan.md`, `Window_filter_tests.md` and the validation logs.

## Quick start

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env                      # then fill in the values; .env is git-ignored
.venv/bin/research-agent db upgrade       # create / migrate the database (SQLite by default, Postgres/Supabase via DATABASE_URL)
.venv/bin/research-agent seed             # load config/ into the database
.venv/bin/research-agent plan             # list the searches a run would make
.venv/bin/research-agent collect          # one daily run: today and the 3 days before it
.venv/bin/research-agent status           # recent runs
.venv/bin/research-agent papers --days 7  # what the last week's runs stored for the profile
.venv/bin/research-agent newsletter build   # this week's digital edition (HTML + text), frozen as an issue; add --week-ending YYYY-MM-DD
.venv/bin/research-agent newsletter publish curt-2026-10-30 --to file   # write the draft files (or --to gmail)
.venv/bin/research-agent api token       # a new random access token for the API (printed once, not stored)
.venv/bin/research-agent api             # start the application API: search and newsletter settings (needs API_TOKENS)
.venv/bin/research-agent providers        # which sources and full-text routes are on, and what a premium key would add
.venv/bin/research-agent pmc-lag         # how long papers take to get a PMC ID after we first store them
.venv/bin/research-agent fulltext         # fetch full text for stored papers (add --dry-run to see the routes first)
```

`papers` hides repository deposits, datasets and book chapters unless you pass `--all-types`. `collect --date 2026-10-30` replays any day; `--sources pubmed,crossref`, `--paths topic,journal` and `--no-enrich` narrow a run.
Re-running a window is safe: papers are deduplicated, so a repeat adds nothing.

## Providers, tiers and premium keys

Nothing in the pipeline assumes free-tier access. Every external service is a *provider* with a tier, a set of capabilities and the credentials it
needs. `research-agent providers` shows what is on, what is off and what each upgrade would change. Put a key in `.env` and the matching route
switches on at the next run; no code changes.

| Credential | Effect |
|---|---|
| `NCBI_API_KEY` | PubMed at 10 requests/s |
| `OPENALEX_API_KEY`, `OPENALEX_PLAN=premium` | exact created-date filtering instead of the publication-date lookback (falls back by itself if the key is refused) |
| `CROSSREF_PLUS_TOKEN` | Crossref at a higher rate |
| `SPRINGER_API_KEY` | open access Springer / BMC / Nature full text |
| `ELSEVIER_API_KEY` (+ `ELSEVIER_INST_TOKEN`) | ScienceDirect full text; paywalled papers need an institutional entitlement |
| `WILEY_TDM_TOKEN` | Wiley full text as PDF |
| `FULLTEXT_ORDER` | override the order full-text providers are tried in |

**Full-text routing.** `research-agent fulltext` tries the active providers in order (Europe PMC XML, Springer open access, Elsevier, Wiley, free PDF
copies) and stops at the first that returns the paper's text. Every attempt is recorded; a provider that could not help is not asked again for
`fulltext.retry_days`, and a provider added later is tried on the papers earlier providers missed. `fulltext --dry-run` shows the route per paper.

**Papers that reach PubMed Central (or get a free copy) later.** PMC lags publication and embargoes lift, so a paper with no PMC ID today may have one in a
few weeks. Every `fulltext` pass first re-looks-up, in Europe PMC (PMC ID) and OpenAlex (free-copy links), the papers that have no full text and no PMC ID, at most
once per `fulltext.retry_days` (7) and only for `fulltext.recheck_days` (90) after the paper first entered the index; papers that then have a route are fetched in
the same pass. Run `fulltext` daily after `collect`. Each pass is logged as a `fulltext` run (`research-agent status`), including how many new PMC IDs it found, and `research-agent pmc-lag` reports how long papers took to get a PMC ID (`papers.pmcid_found_at` minus `papers.first_seen_at`) and how old the ones still without one are.

**Printing and licences.** Owner decision (2026-10-31): licences do not limit printing. Every full text the agent holds is printable, whether it carries an open licence, a restrictive one (CC BY-NC, -ND) or came through a paid entitlement. The licence, rights class (`open` / `entitled` / `unknown`), provider and tier are still recorded for each text. The stricter rules (only listed licences; paid text only if allowed) remain available as `fulltext.print_text_regardless_of_licence: false` plus `print_licences` and `allow_entitled_in_print`, for another profile. After changing the policy, `research-agent fulltext --reapply-policy` recomputes which stored texts are printable (no network). The premium adapters (Elsevier, Wiley, OpenAlex created-date, Crossref Plus) were written from the publishers' documentation and are tested against a mocked network only; the first run with a real key is their real test.

**Abstracts recovered from the paper's own text (rare case).** A paper with no abstract anywhere else (some PDF-only or undecided papers) has its abstract looked for in its PDF or full text, in the first pages: an "Abstract" heading, a "Summary" heading, a structured Background/Methods/Results/Conclusions block, the block just before the Keywords line, and as a last resort the opening of the Introduction. The result is stored with where it came from (`papers.abstract_source`) and the paper is judged again (relevance gate, subjects, track); fetched papers whose relevance was undecided are included for this. A text is only accepted if it really is that paper (its DOI, or most of its title words, appears in the first pages): an index once supplied a link to a different article. `research-agent fulltext --recover-abstracts` recovers abstracts from texts already stored; `--verify-stored` removes stored texts that are not their paper (both need no network).

**Adding a provider.** Write a class with `name`, `tier`, `priority`, `info()`, `active()`, `supports(request)` and `fetch(request)` (see
`infrastructure/fulltext/providers.py`), register it in `infrastructure/registry.py`, and add tests. A discovery source is the same idea
(`ports.DiscoverySource`) plus the specs the planner should build for it.

## The weekly digital edition

`newsletter build` selects papers by **the date our database first stored them** (`papers.first_seen_at`), not by publication or issue date, groups them
(retractions, direct research by subject, related mechanisms, preprints, trials), writes HTML and plain text, and freezes the result as an *issue*
with its items. Rules:

* A paper is carried by at most one edition (a database constraint enforces it). A retraction alert about a paper an earlier edition carried is allowed once.
* Retracted papers get their own section at the top; a retraction notice is attached to its original, never listed alone (and dropped if we hold no original).
  Papers sent in the last `recheck_retraction_weeks` weeks are re-checked at Crossref and OpenAlex before each build.
* Excluded by default: repository deposits, datasets, book chapters, **errata/corrections and letters** (reader responses, replies, comments, editors' notes; a paper typed 'Letter' that has a real abstract is kept as a research letter), non-English papers, papers whose relevance could not be judged. All stay in the database and the app; `digest.exclude_content_types` in the profile changes the list.
* **Papers published long before we found them are left out** (owner decision 2026-10-31): `newsletter.freshness.max_publication_age_days` (default 7; `null` = no limit) is the number of days between a paper's publication date and the day our database first stored it. It is measured from when we stored the paper, so a paper held back by a cap is not penalised for waiting. Unknown dates are never left out, a month-only date counts as the last day of that month (`2026 Oct` is kept in late October, `2026 Jul` is not), and retraction alerts are exempt. Left-out papers stay in the database and the app; the build output and the issue's stats say how many.
* **"Full text available" label**: a paper whose full text we hold and the print policy allows carries that label in the digital edition, meaning the printed edition carries it in full. Papers whose abstract was recovered from a PDF are labelled "Abstract recovered from the PDF" (or "No abstract: summary from the introduction").
* **Ranking** (what fills a capped section) is a set of weights in `newsletter.ranking` of the profile: watched journal 3, watched researcher 3, **printable full text in hand 3**, field word in the title 2, matching subjects 1 each (up to 3), has an abstract 1, core journal 1. Raise or lower any of them; 0 switches one off. Full text counts only if it has been fetched before the edition is built, so run `fulltext` first.
* **Summaries** come from the Groq API (any OpenAI-compatible endpoint; same `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` settings as the earlier newsletter agent),
  paced at one request per 2.1 s with retries. The model sees only the title and abstract, treated as untrusted text, and must keep every number exactly. Each
  answer is checked: not empty, not too long, no links, and **every number in it must appear in the abstract**; one retry, then the edition falls back to an
  extract of the abstract. Good summaries are cached per paper (a rebuild makes no model calls); failures are not. The edition's footer says when summaries
  are model-written, and each stored item records which kind it got. With no model configured, or `--no-llm`, extracts are used.
* The first edition covers `lookback_days` (7); later ones reach back to the last edition but never past `max_catchup_days` (28), so a missed week or a section cap loses nothing.
* `build` is idempotent per week; `--rebuild` replaces an unpublished edition; published or sent editions are never rebuilt.
* `publish --to file` writes `newsletter.html/.txt/.eml` under `outputs/`; `--to gmail` creates a Gmail **draft** once per issue. The program never sends mail. (Google's `gmail.compose` permission would technically allow sending, because Google has no draft-only permission; the code only ever creates drafts, and the permission cannot read the mailbox.) `mark-sent` records that a person sent it.
* **Edition size is capped, and the caps are settings** in the profile (`newsletter.caps` in `config/profiles/curt.yaml`): `per_section` (default 30 direct, 15 mechanisms, 10 preprints, 10 trials; a missing or null section is uncapped), `priority_to_watched` (default true: papers from a watched journal or researcher are chosen before any other when a cap bites, and are the last to be trimmed by the size limit), `total` (optional), and `max_html_kb` (95: if the HTML is still larger, the lowest-ranked papers are trimmed until it fits, because Gmail clips messages near 100 KB). Retractions are never trimmed. Papers held back are not marked as sent: they are considered again next week for up to `max_catchup_days` (28), after which they age out. The footer says how many were held back; the build output and the issue's stats say why.
* The printable PDF edition is **not built**: its template has not been supplied yet. Each stored issue item already records the paper's full-text status for it.

## The application API

**Web page.** Start the API (`research-agent api`) and open `http://127.0.0.1:8000/app`: a search form (words, author, journal, followed researcher, publication dates, full text, sort) and a Newsletter settings tab with tick boxes for topics, journals, researchers and sources. Paste an access token (it stays in that browser tab only); an admin token is needed to save settings. The page is served by the API itself (no separate hosting or CORS), holds no data, runs only its own script (strict content security policy) and never builds page content from HTML strings. It works on a phone-width screen and in light or dark mode.

`research-agent api` serves what the daily and weekly jobs have already stored, for any front end (web, phone, a reading app). It does three things: it **searches** the
stored papers, it shows the weekly editions and the system's health, and it lets an administrator **switch search topics, journals, researchers and sources on or off for
the newsletter**. It never searches the sources and never changes a paper: its data connection is opened read-only. The only thing it can write is the newsletter's switches
(below). Routes are all under `/v1` and need `Authorization: Bearer <token>`, except `/health` and `/ready`.

| Route | What it does |
|---|---|
| `GET /v1/papers` | **the search** (see below) |
| `GET /v1/papers/filters` | what a search form can offer: journals in the database with counts, the followed journals and researchers with counts, date ranges, sort choices |
| `GET /v1/papers/{id}` | one paper: abstract, links, subjects, which searches found it, retraction notice, which editions carried it |
| `GET /v1/papers/{id}/fulltext` | the full text we hold (`?format=text` for plain text), with provider, licence and whether it is printable |
| `GET /v1/newsletter/config` | which topics, journals, researchers and sources the newsletter is built from (any token) |
| `PATCH /v1/newsletter/config` | switch several on or off at once (admin token) |
| `PUT /v1/newsletter/config/{kind}/{key}` | switch one on or off (admin token); `kind` is `subjects`, `journals`, `researchers` or `sources` |
| `DELETE /v1/newsletter/config` | everything back on (admin token) |
| `GET /v1/retractions` | retracted papers with their notice |
| `GET /v1/issues`, `/v1/issues/{key}`, `/v1/issues/{key}/html`, `/v1/issues/{key}/text` | the weekly editions, with what was held back or left out |
| `GET /v1/stats`, `/v1/runs`, `/v1/profile` | overview and health: counts, last collection and full-text runs, recent run statuses, the followed journals, researchers and subjects |

**Search (`GET /v1/papers`).**
* *Date:* `published_from` / `published_to` (publication date, `YYYY-MM-DD`). `days` and `since` filter by the date our database first stored the paper. A paper whose source gave
  only a month or year has no exact date: it is left out of a published-date range and comes last when sorting by publication date.
* *Journal:* `journal=cephalalgia` (a followed journal's key: matched by ISSN or its exact name, so `neurology` does not return Frontiers in Neurology) or any text that is part
  of a journal name (`journal=frontiers`). Repeat the parameter for several.
* *Author:* `author=goadsby p` finds papers whose author list contains every word, ignoring case and accents (`muller` finds Müller). Repeat for several authors.
* *Researcher:* `researcher=peter_j_goadsby` (key or full name from `/v1/papers/filters`). This is the identity-checked link (ORCID / OpenAlex id), not a text match on names;
  a link that is only a name match is not counted. An unknown researcher is an error (422).
* *Also:* `q` (every word in title or abstract), `has_full_text`, `track`, `kind`, `content_type`, `watched`, `retracted`, `include_hidden`, `limit` (max 200), `offset`.
* Different filters combine with AND; repeated values of one filter combine with OR.
* *Sort:* `sort=newest` (when we stored it, the default), `published` (publication date), `relevance`, `fulltext` (papers whose full text we hold first, then newest), with
  `direction=desc` (default) or `asc`. Relevance adds, for each `q` word, 3 points for the title and 1 for the abstract, plus the profile's ranking weights for a followed
  journal or researcher and for held full text (`newsletter.ranking.weights`, 3 each by default); ties go to the more recently stored. Every item carries its `relevance`.

**Newsletter settings.** Everything is on until switched off. A switch changes only which stored papers the next newsletter picks; it does not change the daily collection, the
database or the search (and the stored "watched" labels stay as they are). A paper is kept while at least one of its routes in is still on: one of its matched topics, a followed
journal, or a followed researcher (a paper with none of these is not affected). With any source switched off, a paper must also have been found by at least one source that is
on (switching ClinicalTrials.gov off therefore removes the trials section). A journal or researcher switched off also loses its "watched" label and priority. Retraction alerts
are never filtered. Papers left out stay unsent, so switching something back on lets a later edition (within the 28-day catch-up) carry them. You cannot switch off every source,
or every topic, journal and researcher together. Each edition records what was off (`left_out_by_selection`, `selection`), and its footer names only the sources that were on.
Body example: `PATCH /v1/newsletter/config` with `{"journals": {"cephalalgia": false}, "sources": {"openalex": false}}`; a bad key or an unsafe result changes nothing.

Safety: the API refuses to start without `API_TOKENS` (comma separated for several clients); tokens are compared in constant time; `/docs` is off unless `API_DOCS=1`;
responses are never cached; CORS allows only the origins in `API_CORS_ORIGINS`; the same hidden types as the digest (deposits, datasets, book chapters, letters, corrections)
are left out unless `include_hidden=true`. Changing the newsletter settings needs a separate admin token (`API_ADMIN_TOKENS`); with none set, nobody can change them (403). Those changes
use their own database connection (`API_ADMIN_DATABASE_URL`, default `DATABASE_URL`) that no data route touches; on Supabase give it a role that can only write the
`newsletter_selections` table, and give the data connection a role that can only read. It serves one profile (`API_PROFILE`, default `curt`); accounts and per-user views are
v2.0. Run it behind HTTPS (the API does not terminate TLS itself).

**Limits.** Each client address may make `API_RATE_LIMIT_PER_MINUTE` requests a minute (default 120; `/health` and `/ready` are exempt); beyond that it gets 429 with `Retry-After`. After `API_AUTH_FAILURES_PER_MINUTE` wrong tokens (default 10) from one address, further wrong or missing tokens from it get 429 for the rest of the minute; a valid token is never refused because of this, so nobody can lock real users out. The client address is the connecting address unless `API_CLIENT_IP_HEADER` names a header your host's proxy sets (Railway: `X-Real-IP`; a comma list uses its last entry); `GET /v1/whoami` shows what the server sees, so the setting can be checked after deploying. The limiter lives in one process, so run one replica.

**Database roles (Supabase).** The jobs (collection, full text, newsletter) use `DATABASE_URL`, the owner. The API should not. `research-agent db roles` creates two limited roles (`--print-sql` shows the SQL first): `curt_api_read` (SELECT on the ten tables the API shows; its sessions are read-only) and `curt_api_config` (read and write on `newsletter_selections` only). Each gets an explicit row-level-security policy (no BYPASSRLS, no superuser), random passwords, and the two connection strings are written into `.env` as `API_DATABASE_URL` and `API_ADMIN_DATABASE_URL` without being shown. Running it again rotates the passwords.

## Gmail drafts (one-time setup)

The weekly job can put the edition in a Gmail **draft** for a person to review and send. Setup, once:
1. In the Google Cloud console create a project and enable the **Gmail API**.
2. Google Auth Platform (OAuth consent screen): user type **External** (an ordinary Gmail account) or **Internal** (a Google Workspace organisation); add the scope `https://www.googleapis.com/auth/gmail.compose`.
3. **Publish the app** (Audience > Publish app, status *In production*) *before* signing in. While an external app stays in *Testing*, Google expires the refresh token after 7 days and the weekly draft would stop. An unpublished-for-review app shows an "unverified app" warning at sign-in; that is expected for an app only you use.
4. Create an OAuth client of type **Desktop app**. Put its ID and secret in `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
5. `pip install -e ".[gmail]"`, then `research-agent gmail authorize`. Sign in as the Gmail account that should hold the drafts. The refresh token goes straight into `.env` (not shown).
6. Copy the three `GOOGLE_*` values into the weekly-newsletter service's variables. The next weekly run then adds a draft step. Set `NEWSLETTER_RECIPIENTS` (comma separated plain addresses) in the same place to address the draft; it overrides `newsletter.recipients` in the profile, and is kept out of git on purpose. With neither set the draft has no "To" address.

## Deployment and scheduling

The API, the web page and both scheduled jobs are set up for Railway (one `Dockerfile`, three services: `api`, `daily-collect` running `research-agent job daily`, `weekly-newsletter` running `research-agent job weekly`), with Supabase as the database (use its pooler: the direct address is IPv6 only). `research-agent status --max-age-hours 36` fails when no successful collection has finished recently, and the weekly job uses it as a guard. The weekly edition is stored in the database and shown in the web page's Editions tab. Step-by-step instructions, the variables to set and what is not yet verified are in `DEPLOYMENT.md`.

## How a run works

1. **Plan** searches from the field and profile (`application/planner.py`): topic, journal, researcher, preprint and trial paths.
2. **Discover**: each source runs in its own thread with its own rate limit; one failing source never stops the others.
   Freshness filters use each source's *entry* date (PubMed `edat`, Europe PMC `FIRST_IDATE`, Crossref created, ClinicalTrials.gov first post).
3. **Gate**: a record must mention the field's gate term (`migrain*`) in its title or abstract.
4. **Merge**: one `papers` row per paper (DOI, then PMID, then title), one `paper_hits` row per time a search found it.
5. **Match** to the profile: gate, language, subjects, track (A/B), and which watched journals/researchers it came from.
6. **Enrich**: abstract for papers the gate could not judge, retraction state, licence and free links.
7. **Log**: `runs` and `source_run_log` record every search, its counts and any error or silent-zero alarm.

## Layout

```
config/fields/*.yaml      what a field means (gate terms, subjects, bridge list)
config/profiles/*.yaml    one profile's chosen journals, researchers and subjects
src/research_agent/
  domain/                 data types, gate and classification, config models, retraction title signals
  application/            planner, ingest pipeline, profile matching
  infrastructure/         http client, database (models, repository, migrations helper), one adapter per source
  bootstrap.py cli.py     wiring and command line
migrations/               Alembic (Postgres gets row level security enabled for Supabase's REST API)
tests/                    420 offline tests (real API responses in tests/fixtures); `pytest -m live` hits the real services
```

## Adding another profile or field

Copy `config/profiles/curt.yaml` and `config/fields/migraine.yaml`, change the gate terms, journals, researchers and subjects, then run
`seed --profile <id>` and `collect --profile <id>`. Nothing in `src/` mentions migraine.

## Notes

* Secrets are read from `.env` and are scrubbed from every error message and log line.
* OpenAlex is metered; the adapter stops before the daily allowance runs out and says so in the run log.
* Publisher RSS feeds, the print (PDF) edition, scheduling and any user-facing front end are later phases.
