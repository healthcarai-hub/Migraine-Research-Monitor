# 6. Decision log

> **In short.** The important design choices, each with the situation that forced it, what was decided, what it costs, and what was considered instead. Where a decision came from the project owner it says so. Read this to understand *why the system is shaped the way it is*, and to know which choices are safe to revisit.

| # | Decision |
|---|---|
| [D1](#d1-a-modular-monolith-with-three-entry-points) | A modular monolith with three entry points |
| [D2](#d2-cron-jobs-and-a-shared-database-instead-of-a-queue) | Cron jobs and a shared database instead of a queue |
| [D3](#d3-hosting-on-railway-and-supabase) | Hosting on Railway and Supabase |
| [D4](#d4-one-schema-for-postgresql-and-sqlite) | One schema for PostgreSQL and SQLite |
| [D5](#d5-select-papers-by-when-we-first-stored-them) | Select papers by when we first stored them |
| [D6](#d6-idempotent-ingest-with-identity-merge) | Idempotent ingest with identity merge |
| [D7](#d7-a-relevance-gate-and-the-union-of-sources) | A relevance gate and the union of sources |
| [D8](#d8-the-program-only-creates-drafts) | The program only creates drafts |
| [D9](#d9-frozen-editions-and-the-never-twice-constraint) | Frozen editions and the never-twice constraint |
| [D10](#d10-least-privilege-database-roles) | Least-privilege database roles |
| [D11](#d11-keyword-search-first) | Keyword search first |
| [D12](#d12-gmail-safe-html) | Gmail-safe HTML |
| [D13](#d13-newsletter-switches-are-separate-from-collection) | Newsletter switches are separate from collection |
| [D14](#d14-additive-changes-and-no-migration-when-avoidable) | Additive changes, and no migration when avoidable |
| [D15](#d15-serve-the-web-app-from-the-api) | Serve the web app from the API |
| [D16](#d16-date-the-edition-for-delivery-not-for-the-build) | Date the edition for delivery, not for the build |

## D1. A modular monolith with three entry points

* **Context.** Three kinds of work (daily collection, weekly newsletter, serving the API) with different rhythms, but small data and one team.
* **Decision.** One codebase and one Docker image, started three ways. Inside, a ports-and-adapters layout keeps the rules independent of the outside world.
* **Consequences.** One thing to build, test and version; no network calls between our own services. A shared database is the only coupling.
* **Alternatives.** Separate services per job (more deployment and failure modes for no benefit at this size).

## D2. Cron jobs and a shared database instead of a queue

* **Context.** Work happens daily and weekly; a failure only means "run it again".
* **Decision.** Short-lived jobs started by the host's cron, passing data through the database; no message broker, no worker fleet.
* **Consequences.** Simple to run and reason about. Every step must be idempotent, which it is (see [chapter 5](05-reliability-security-operations.md#52-idempotency-where-re-running-is-safe)). A hung job needs its own timeout because the host will not stop it.
* **Alternatives.** A task queue with workers (useful if full-text fetching ever becomes the bottleneck).

## D3. Hosting on Railway and Supabase

* **Context.** The first plan used GitHub Actions for the schedule and Vercel for the API. Comparing them showed that one always-on server removes serverless connection and timeout problems.
* **Decision (owner).** Railway for the API and both cron jobs, Supabase for the database; GitHub only runs the tests.
* **Consequences.** One image, three services, one place to read logs. The jobs hold the database owner's credentials; the public API does not (D10). Both services should sit in the region nearest the database: far away, each database call pays the round trip.
* **Alternatives.** GitHub Actions plus Vercel (rejected after comparison).

## D4. One schema for PostgreSQL and SQLite

* **Context.** Tests and local work should run with no server, yet production needs Postgres features such as row level security.
* **Decision.** One SQLAlchemy model, run on both. Use only features both support; for filters that would need database-specific JSON operators, store a small derived text column instead (`author_names`, `entity_tags`).
* **Consequences.** Fast, offline tests; a few denormalised columns to keep in step with their source (maintained by model validators).
* **Alternatives.** Postgres-only (needs a server in every test run); JSON operators per database (branching code).

## D5. Select papers by when we first stored them

* **Context.** Publication dates are unreliable: a paper can reach an index days or weeks after it was published, and some give only a month.
* **Decision (owner).** The newsletter picks papers by `first_seen_at`, the moment our database first stored them, and shows the publication date as information only. Papers published more than 7 days before we found them are left out.
* **Consequences.** A late-indexed paper still reaches an edition exactly once; the window is exact and replayable. Very old papers that surface late are filtered by the freshness rule.
* **Alternatives.** Filtering by publication date (misses late arrivals, double counts corrections).

## D6. Idempotent ingest with identity merge

* **Context.** Five sources overlap heavily and describe one paper with different identifiers; runs overlap by design.
* **Decision.** Merge by DOI, PMID, trial ID and PMCID, then by a title fingerprint guarded against preprint and published pairs. Every identifier column is unique in the database.
* **Consequences.** Re-running a day adds nothing; a failed run can simply be repeated. Merge rules decide which source's value wins per field.
* **Alternatives.** Keep per-source rows and de-duplicate at read time (every query becomes harder).

## D7. A relevance gate and the union of sources

* **Context.** No single source finds more than about half of the new papers, and broad searches return a lot of noise.
* **Decision.** Every search carries the field's gate word; a record without it is set aside. Use the union of all sources and record which source found each paper.
* **Consequences.** Good recall and a clean collection, at the price of searching several APIs and keeping their quirks in adapters.
* **Alternatives.** Trust one source (misses half); no gate (floods the newsletter).

## D8. The program only creates drafts

* **Context.** A newsletter to a client must be read by a person first.
* **Decision (owner).** The publisher creates a Gmail **draft**; a person reviews and sends it. The program has no send path.
* **Consequences.** A bad edition can never reach a reader unreviewed. The Gmail permission used could technically send, so the safeguard is the code, not the scope.
* **Alternatives.** Auto-send (rejected: no human check).

## D9. Frozen editions and the never-twice constraint

* **Context.** Readers must never get the same paper twice, and what was sent must be reconstructable.
* **Decision.** An edition is stored whole (HTML, text, statistics) with a snapshot per paper. A unique key on `(profile, paper, role)` makes a repeat impossible; a published or sent edition is never rebuilt.
* **Consequences.** Strong guarantees, and the Editions tab is a plain read. Fixing a published edition means issuing a new one, not editing history.
* **Alternatives.** Re-render editions from live data (sent content could silently change).

## D10. Least-privilege database roles

* **Context.** The public API is the exposed surface; the jobs are not.
* **Decision.** Three roles: the owner for the jobs, a read-only role for the API's data routes, and a switches-only role for the one write route. Row level security on every table; read-only transactions on the data connection.
* **Consequences.** A compromised API process cannot alter papers or editions. More setup (a command creates the roles), and a new table needs its grants.
* **Alternatives.** One shared credential (simplest, but the exposed process could damage the data).

## D11. Keyword search first

* **Context.** Users need to find papers by words, journal, researcher, source and topic. Semantic and full-text search are attractive but add infrastructure.
* **Decision (owner).** Ship plain keyword matching on titles and abstracts, with exact filters; record full-text search and meaning-based search as v2.0 ideas.
* **Consequences.** Predictable, explainable results and no extra infrastructure; no synonyms and no search inside the text yet.
* **Alternatives.** Vector search now (weak on exact drug and gene names, needs an embedding model); a text-search index now (planned as the first v2.0 step).

## D12. Gmail-safe HTML

* **Context.** The first newsletter design used a `<style>` block and CSS classes; it looked fine in a browser and poor in Gmail's compose window, which drops style blocks.
* **Decision.** Build the HTML as nested tables with every style inline, in one 760 px column, and keep a size cap under Gmail's clipping limit.
* **Consequences.** It renders reliably where it matters; the markup is verbose and less elegant than modern CSS.
* **Alternatives.** Modern CSS (breaks in the target client); images of text (not accessible).

## D13. Newsletter switches are separate from collection

* **Context.** The team wants to tune what the newsletter picks without changing what the system collects or what the search shows.
* **Decision (owner).** A selection layer applied only when building an edition; switches live in their own table; collection and search ignore them.
* **Consequences.** Safe to experiment: nothing is lost by switching something off, and papers held back for that reason can return within the catch-up window. A change that would leave nothing to select is refused.
* **Alternatives.** Change the collection config (loses data and needs a deploy).

## D14. Additive changes, and no migration when avoidable

* **Context.** The ORM selects every column, so a column added before its migration runs breaks the live API, whose read-only role cannot migrate.
* **Decision.** Prefer additive migrations applied at job start, and prefer designs that need no new column. The topic filter, for example, matches the existing subjects JSON as text instead of adding a column.
* **Consequences.** Deploys are low-risk; a few filters rely on a small, tested text-matching convention.
* **Alternatives.** A proper column or index for every filter (cleaner, but riskier to deploy).

## D15. Serve the web app from the API

* **Context.** A small internal tool should not need its own hosting.
* **Decision.** The API serves a static page that holds no data and runs only its own script, under a strict content security policy.
* **Consequences.** No CORS, no second deployment, a small attack surface. The page is plain HTML and JavaScript rather than a framework build.
* **Alternatives.** A separate single-page app host (more moving parts).

## D16. Date the edition for delivery, not for the build

* **Context.** The edition is built on Thursday evening in India but goes out on Friday.
* **Decision (owner).** A profile setting names the edition's weekday; the date is the next such day on or after the run, while the content is everything stored up to the build.
* **Consequences.** The label matches when readers get it; Friday's collection goes into the next edition, and nothing is lost or sent twice.
* **Alternatives.** Dating by the build day (reads as a day early); building on Friday morning (less time to review).
