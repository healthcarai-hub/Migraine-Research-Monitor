# Getting started and configuration

How to run the service locally, which commands exist, and what you can configure. For what the system *is*, read the [README](../README.md); for how it is built, the [architecture study](architecture/README.md); for hosting it, [DEPLOYMENT.md](../DEPLOYMENT.md).

## Run it locally

You need Python 3.12 (the commands below use [uv](https://docs.astral.sh/uv/), but any virtual environment works).

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env                       # fill in what you have; every credential is optional
.venv/bin/research-agent db upgrade        # create the database (SQLite by default; Postgres via DATABASE_URL)
.venv/bin/research-agent seed              # load config/ into the database
.venv/bin/research-agent collect           # one daily run
.venv/bin/research-agent newsletter build  # build this week's edition
.venv/bin/research-agent api token         # make an access token (printed once)
API_TOKENS=<that token> .venv/bin/research-agent api   # API and web app at http://127.0.0.1:8000/app
.venv/bin/python -m pytest                 # run the tests (offline)
```

The package and command are still called `research_agent` and `research-agent`, from the project's first design.

## Commands

| Command | What it does |
|---|---|
| `research-agent db upgrade` | create or migrate the database |
| `research-agent seed` | load `config/` into the database |
| `research-agent plan` | list the searches a run would make |
| `research-agent collect` | one daily run; `--date 2026-10-30` replays a day, `--sources` and `--paths` narrow it |
| `research-agent fulltext` | fetch full text (add `--dry-run` to see the routes first) |
| `research-agent newsletter build` | build an edition; `--week-ending YYYY-MM-DD` picks the date, `--rebuild` replaces an unpublished one |
| `research-agent newsletter publish <key> --to file` or `--to gmail` | write draft files, or create the Gmail draft |
| `research-agent job daily` / `job weekly` | what the hosting cron runs |
| `research-agent status` | recent runs |
| `research-agent papers --days 7` | what the last week stored |
| `research-agent providers` | which sources and full-text routes are on |
| `research-agent api` / `api token` | start the API and web app; make an access token |
| `research-agent gmail authorize` | one-time Gmail sign-in |
| `research-agent db roles` | create the limited database roles for the API (Supabase) |

## Configuration

* **`config/fields/*.yaml`**: what a field means: the gate word, topics and the list of mechanism links.
* **`config/profiles/*.yaml`**: one profile's journals, researchers, newsletter wording, colours, caps, ranking weights and timing.
* **`.env`**: credentials and hosting settings. Copy `.env.example`; every credential is optional, and a present one switches on its route. Names only:

| Group | Variables |
|---|---|
| Database | `DATABASE_URL`, `API_DATABASE_URL`, `API_ADMIN_DATABASE_URL` |
| Sources | `CONTACT_EMAIL`, `NCBI_API_KEY`, `OPENALEX_API_KEY`, `SPRINGER_API_KEY`, `CROSSREF_PLUS_TOKEN` |
| Summaries | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| Gmail | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `NEWSLETTER_RECIPIENTS` |
| API | `API_TOKENS`, `API_ADMIN_TOKENS`, `API_RATE_LIMIT_PER_MINUTE`, `API_CLIENT_IP_HEADER`, `API_DOCS` |
| Jobs | `JOB_OVERLAP_DAYS`, `JOB_FULLTEXT_LIMIT` |

Secrets are read from `.env` (which is git-ignored) and scrubbed from errors and logs. Every setting and its meaning is in the [reference](REFERENCE.md); Gmail setup is covered there too.

## Watch another field or client

Copy `config/profiles/curt.yaml` and `config/fields/migraine.yaml`, change the gate word, journals, researchers and topics, then run `seed --profile <id>` and `collect --profile <id>`. Nothing in `src/` mentions migraine.

## Call the API

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://<host>/v1/papers?journal=cephalalgia&source=pubmed&published_from=2026-10-01&sort=relevance"
```

Set `API_DOCS=true` to serve the OpenAPI docs at `/docs`. The routes, search semantics, access rules and error shapes are described in [API design](architecture/03-api-design.md).

## Deploying

See [DEPLOYMENT.md](../DEPLOYMENT.md): Railway and Supabase setup, schedules, first checks, and what to do when a job fails. The deployment topology is in the [high-level design](architecture/01-high-level-design.md#19-deployment).
