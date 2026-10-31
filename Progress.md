# Progress: Curt Migraine Monitoring

_Last updated: 2026-10-31. The detailed running log of every note and decision is kept in `Progress_history.md`; this file is the clean summary._

## 1. Where the project stands

A field-configurable research-monitoring agent whose first profile is **Curt (migraine)**. It collects new papers every day, keeps them in a database, and every week builds a newsletter that lands in Gmail as a **draft** (the program never sends anything). A small web application lets the team search the database and switch topics, journals, researchers and sources on or off for the newsletter.

| Part | Status |
|---|---|
| Daily collection (PubMed, Europe PMC, Crossref, OpenAlex, ClinicalTrials.gov, preprints) | **Built, deployed, scheduled** |
| Full-text stage (free full text, abstract recovery from the full text) | **Built, deployed** |
| Weekly digital newsletter (HTML and text, Gmail draft) | **Built, deployed, scheduled** |
| Application: API, web page (Search, Editions, Newsletter settings) | **Built, deployed** |
| Hosting: Railway (API and two cron jobs) and Supabase (database) | **Live**; first scheduled runs still to be watched |
| Printable PDF edition | **Not started: waiting for the client's template** |
| Multi-user sign-up and other fields (v2.0) | Deferred on purpose |
| Search inside the full text and search by meaning (v2.0) | Parked for later; see section 8 |

Quality: 536 offline tests and 1 live test pass. Every run, search and decision below was checked against the real services; nothing was bought and no secret is written in any log.

## 2. What was done, in phases

| Phase | What | Result |
|---|---|---|
| **1. Source validation** (30 Oct) | Read the client's workbook (10 journals, 30 researchers, 10 discovery sources, 12 subject rows) and tested every resource live: reachability, limits, cost, identity. Logs: `Journal_source_validation.md`, `Researchers_source_validation.md`, `Discovery_API_check.md`, `Window_filter_tests.md`. | All 30 researchers resolved to an ORCID and OpenAlex ID and confirmed by the owner. Reliable free sources found; Google Scholar and Cochrane cannot be scripted (PubMed stands in for Cochrane). |
| **2. Design** (30 Oct) | `Pipeline_design_plan.md`: two parts (daily collection, weekly newsletter), database design, rules for flags and retractions, build order. Owner decisions recorded (section 5). | Scope fixed: Curt only in v1.0, the application side included. |
| **3. Collection pipeline** (30 Oct) | Project skeleton, database schema and migrations, three search paths per source (topic, journal, researcher), the "migraine" gate, de-duplication, retraction flags from three sources, preprints and trials, content types (deposits and datasets stored but kept out of digests). | 94 searches per run; about 20 new papers a day; a repeat run adds nothing. |
| **4. Full text and enrichment** (30 to 31 Oct) | Provider tiers (Europe PMC full text, Springer Nature open access, free PDFs), PDF text extraction, abstracts recovered from the full text (also for editorials), repair of words glued together in abstracts. | About half of recent papers carry full text; papers with no abstract are listed last in their section. |
| **5. Weekly newsletter** (31 Oct) | Selection by the date we first stored a paper, never resent, 7-day freshness limit, size caps, held-back papers return once and rank lower, AI summaries with checks (Groq), retractions section, Gmail draft. Layout rebuilt for Gmail (tables with inline styles, header and category headings like the older newsletter), the **At a glance** summary panel, one navy-and-blue colour scheme, outro signed by the Healthcar AI Research Team. | A real weekly edition builds in about 7 minutes and arrives as a Gmail draft. |
| **6. Application** (31 Oct) | Read-only token-protected API, one search endpoint (date, source, journal, researcher, topic, author, full text, sorts), newsletter on/off switches stored separately from the collection, web page with coloured chip filters, Editions tab with a formatted preview in a locked frame. Rate limiting, wrong-token lockout and limited database roles. | Everything is served by the API itself at `/app`. |
| **7. Hosting and scheduling** (31 Oct) | Railway with three services from one Docker image; Supabase through the session pooler; GitHub only runs the tests; Gmail sign-in done once. Speed work (batched database calls, a commit per search) took the daily run from about 23 minutes to about 2. | Daily collection 05:30 UTC (11:00 IST). Weekly job Thursday 14:30 UTC (20:00 IST), edition dated Friday, 12-hour freshness guard. See `DEPLOYMENT.md`. |
| **8. Print edition** | PDF with full text, QR code and DOI line per paper, retracted section. | **Not started** (see section 3). |

How this lines up with the original build order in the design plan: its phases 0 to 4 and 6 (scheduling) are done; phase 5 (print) is the only one not started; "monitoring" (watching real unattended runs) begins with the first scheduled runs.

## 3. Open items

| Item | Kind | Notes |
|---|---|---|
| **Printable PDF edition** | Waiting on the client | Needs their template. Also unanswered: papers per edition and who chooses them, large print or high contrast and the font size, figures and tables. Full text, the "printable" ranking signal and the rule that licences do not limit printing are already in place. |
| Feed URLs for JAMA Neurology and Nutrients | Question for the client | Both are covered through the databases only today. |
| What "abstract-only" means in rule 8 of the instructions sheet | Question for the client | Our reading: only the abstract is readable. |
| Track B bridge list: who approves it, and a starting list | Question for the client | Today a "mechanism" paper needs "migraine" in the title or abstract, or a bridge entry. |
| Papers only Crossref or OpenAlex index (low-profile journals): include, with what quality check | Question for the client | |
| Are the Scholar alert emails still wanted as an extra source | Question for the client | |
| Core and Targeted wording under the Monitored journals table | Owner to confirm | Draft text in `newsletter.priority_notes` in `config/profiles/curt.yaml`. |
| Three choices made on the owner's behalf: switching a journal off removes only that route in; "sources" means the five discovery databases; search relevance is a simple points score | Owner to confirm | Change on request. |
| Watch the first real runs: daily on 1 Nov, weekly on 5 Nov (draft dated 6 Nov) | To check once live | The log shows each step with a time. |
| Failure alerts in Railway | To check | Not verified on the owner's plan. |
| Gmail OAuth app set to "In production" | To check | Otherwise Google expires the token after about 7 days and the weekly draft fails. |
| Multi-user sign-up, other fields, per-user sources | Deferred (v2.0) | The design keeps this possible. |
| Full-text keyword search and meaning-based (vector) search | Deferred (v2.0) | Plan and order in section 8. |

## 4. How it runs day to day

| Service | Runs | Does |
|---|---|---|
| `api` | always | The API and the web page at `/app`. |
| `daily-collect` | `30 5 * * *` UTC | Collects new papers (looks back 3 days, so one failed day is recovered). |
| `weekly-newsletter` | `30 14 * * 4` UTC (Thursday 20:00 IST) | Checks a collection ran in the last 12 hours, fetches full text, builds the edition, creates the Gmail draft. |

An edition is **dated Friday** but holds everything stored up to the Thursday build. The Editions tab labels a built edition "Draft not created yet", then "Published" once the Gmail draft exists, and "Sent" only if someone marks it sent by hand. Setup, settings and recovery steps are in `DEPLOYMENT.md`.

## 5. Decisions made by the owner

| Area | Decision |
|---|---|
| Scope | Curt only in v1.0; the application side is in v1.0; multi-user is v2.0. English only. |
| Sources | PubMed stands in for Cochrane. Track B journals stay. Preprints from the extra servers are included and labelled. No Wiley or Elsevier keys; their papers stay "abstract, QR code and links". Retracted papers are kept and flagged, and a retraction of something already sent is always announced. |
| Collection | 3-day overlapping window; no weekly cross-check. Papers are chosen by the date we first stored them. |
| Newsletter content | Never resend a paper. Leave out papers published more than 7 days before we found them. Edition size is capped; a held-back paper returns once, ranked lower, then drops out. Papers from monitored journals and researchers are chosen first. Papers with no abstract go last. Summaries come from Groq. The program creates drafts only. |
| Printing | Licences do not limit printing full text. The print edition waits for the client's template. |
| Newsletter look | Header, title and category headings follow the older newsletter. At a glance panel with Retrieval sources, Monitored journals (with what Core and Targeted mean) and Monitored researchers, listing only those with publications. Navy and blue colours throughout. A box under At a glance links to the web app (there only, not in the outro; the address is `newsletter.app_link.url` in the profile and the email never carries a token). The outro is signed "Healthcar AI Research Team" in large navy type with an accent bar. |
| Timing | Weekly job Thursday 20:00 IST; edition dated Friday; freshness guard 12 hours. |
| Application | Chip filters, Published dropdown (last 7 days by default), Full text checkbox, Author box removed. Editions tab with an inline preview and a "Draft not created yet" label. |
| Hosting | Railway for everything, Supabase for the database, GitHub private and used only to run tests. |

## 6. Main findings from the source validation

1. The client's researcher links in the sheet return no results; searches need `Surname Initials`, or better the ORCID and OpenAlex IDs we resolved.
2. Eight of ten journal homepages block scripts, so article data comes from PubMed, Crossref and Europe PMC by ISSN.
3. No single source finds more than about half of the new papers in a window, so the pipeline uses the union of sources.
4. Retraction flags are split across sources; a paper is flagged if any of PubMed, Crossref or OpenAlex says so.
5. OpenAlex and PubMed keys were created and work; the project mailbox is the contact address for Crossref, ORCID and OpenAlex.
6. Volume is modest: about 55 migraine papers a week by PubMed alone, about 20 new papers a day across all sources.
7. Track B is thin; Redox Biology has no migraine papers, so the topic gate does the real work there.
8. "CSD" is ambiguous (only 9% of its PubMed hits mention migraine).

## 7. Caveats

* The validation checks were made from one IP on one day; some blocks may behave differently from a server.
* Publisher terms of use were not reviewed; the owner has decided licences do not limit printing.
* ORCID and OpenAlex agreement corroborates an identity but is not proof; the owner reviewed all 30 profiles.
* A few generic subject terms were not probed, and the rate limits of Europe PMC, bioRxiv/medRxiv and ClinicalTrials.gov were not found (marked unverified in the logs).
* `Pipeline_design_plan.md` still opens with "Plan only: nothing is built"; its sections 13 to 16 hold the current build status.

## 8. Ideas parked for v2.0

**Where search stands today.** The words box is plain keyword matching: every word must appear in a paper's title or abstract (a case-insensitive partial match), and relevance is a simple points score (3 for a word in the title, 1 for one in the abstract, plus a bonus for monitored sources and for held full text). It does not search inside the full text and does not understand meaning (so "headache" does not find "cephalalgia"). All the chip filters, dates and the full text checkbox are exact filters on stored fields.

**1. Full-text keyword search (first step).**
* Looks inside the stored full text, so a term found only in the methods, results or discussion also matches; results can show the matching sentence with the words highlighted.
* Built on the database's own text search, which also matches word forms ("migraine" and "migraines"), allows quoted phrases and ranks by how often the words occur; title counts most, then abstract, then body.
* A checkbox "Also search the full text", off by default, so the current precise behaviour stays.
* Limits: covers only papers where we hold full text (about half; 29% in one scratch copy); still no meaning; broad words match many introductions and reference lists, so phrases and ranking matter.
* Size: the stored text is about 45 KB a paper (3 MB for 67 papers in a scratch copy) and a text-search index adds about the same again. Check it against the Supabase plan's size limit. The index needs no new column, so the live API cannot break between deploy and index.

**2. Meaning-based (vector) search, as an addition and not a replacement.**
* Strong at synonyms, plain-language questions and "papers like this one"; weak at exact terms (drug and gene names, identifiers), less predictable, and needs a score cut-off to keep noise out. Best used together with keyword search, with the two rankings merged into one list.
* Start with one vector per paper from the title and abstract: small, covers every paper (also those with no full text). Keep the vectors in their own table (not new columns) so the live API cannot break.
* Passage-level vectors of the full text only if that is not enough: about 20 to 30 passages per paper, likely over a gigabyte a year with an index at today's volume, and noisy (references, boilerplate) unless passages are chosen carefully.
* An embedding model is needed (a hosted service or a local model; biomedical models such as NCBI's MedCPT exist). Not tested on this data; prices not checked.
* Before committing: an offline comparison on a scratch copy (embed the abstracts with a small local model and compare keyword and meaning-based results for real queries such as "headache" and "magnesium prevention"). It needs a new Python package, so the owner's approval first.

**Suggested order:** full-text keyword search, then title-and-abstract vectors, then merge the two rankings, then full-text passages only if needed.

**Also parked:** multi-user sign-up, other fields and per-user sources (see the design plan, section 12).

## 9. Files

* `README.md`: the friendly overview for developers: what the agent is, flow diagrams of how it works, how to run it. `docs/architecture/` is the design study (high-level design, low-level design, API design, data design, reliability and security, decision log, and a deep dive on data sources and ingestion: what each source gives, how it is queried, the fallbacks, and what was observed), and `docs/GETTING-STARTED.md` has the commands, configuration and local setup, and `docs/REFERENCE.md` is the detailed reference (every setting, the full API, newsletter rules, Gmail setup) and `docs/images/` holds the README screenshots.
* `DEPLOYMENT.md`: Railway and Supabase setup, schedules, first checks, day to day.
* `Pipeline_design_plan.md`: the design, with build status in sections 13 to 16.
* `Progress.md` (this file) and `Progress_history.md` (the detailed log).
* `Journal_source_validation.md`, `Researchers_source_validation.md`, `Researchers_recent_papers.md` (and its PDF), `Discovery_API_check.md`, `Window_filter_tests.md`: the validation logs.
* `src/research_agent/` (code), `config/` (the migraine field and the Curt profile), `migrations/` (database schema), `tests/` (536 offline tests with saved real API responses), `data/` (local SQLite databases, not in git), `outputs/` (design pictures and screenshots reviewed with the owner).
