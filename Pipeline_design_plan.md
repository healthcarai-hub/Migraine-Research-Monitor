# Pipeline Design Plan: Curt Migraine Monitoring

Created 2026-10-30. **Plan only: nothing is built.** Each item is tagged **[Owner]** (decided by the owner), **[Proposal]** (my suggestion, not yet agreed) or **[Open]** (needs an answer). Evidence for source behaviour is in `Journal_source_validation.md`, `Researchers_source_validation.md` and `Discovery_API_check.md`; overall status is in `Progress.md`.

## 1. What the system must do

* **[Owner]** Two parts: (1) a **daily backend job** at a fixed time that searches the sources and logs data and results in a database; (2) a **weekly newsletter job** that reads the whole week's results from the database and builds the newsletter.
* **[Owner]** The newsletter is printed for a reader who cannot use a screen, so the print edition should carry the **full text** of each source. A **digital version** with links is also produced.
* **[Owner]** Where full text is not available: show as much as is openly available (title, authors, abstract) and point to the PDFs and links that exist.
* **[Owner]** The print edition carries a **QR code for every paper** (full text or abstract-only) plus a small **DOI line** under it in case the QR is misprinted.
* **[Owner]** Retracted papers are kept and shown in a **separate flagged section**. A retraction notice attaches to its original paper; a notice for a paper we never sent is shown only if the original was sent or is in the same week's results.
* **[Owner]** English only. 3-day overlapping search window. Delivery uses the existing Gmail-draft route.
* **[Owner, 2026-10-30]** "Two pipelines" means (1) an **application side** that serves users on the go from the database, and (2) the **newsletter**. The daily collection job feeds the database for both (see section 12). *My earlier wording, "daily job + weekly job", described collection and newsletter only; the application side is new.*
* **[Owner, 2026-10-30]** **Multi-user scaling is v2.0.** v1 serves Curt only; keep the architecture **flexible for other profiles** (a user will later pick a field such as migraine or L-theanine, then journals, researchers and search topics from offered options). Nothing multi-user is built in v1 (section 12).
* **[Owner]** The Instructions-sheet rules are a starting set, not exhaustive, and can be changed while building.

The 11 engineering rules from the client's sheet map onto the design as follows.

| Rule | Where it lives in the design |
|---|---|
| 1 Independent paths | Daily job runs journal, researcher and topic searches separately (section 3) |
| 2 Author identity | `researchers` table with owner-confirmed ORCID/OpenAlex IDs |
| 3 Journal identity | `journals` table with ISSN and title aliases |
| 4 Source capture | `papers` and `paper_hits` store DOI, PMID, URL, date, journal, authors, query and match reason |
| 5 Deduplication | DOI, then PMID, then bibliographic match; one paper row, many hits |
| 6 Freshness | Daily run, 3-day overlap, track indexed date versus publication date |
| 7 Track B gate | `migraine` in title/abstract by default; narrow bridge list admits "exploratory" items |
| 8 Quality | Retraction, preprint and full-text-status flags on every paper |
| 9 Reporting | Weekly newsletter job, optional daily intake |
| 10 Scope | No commerce, no patents |
| 11-13 Seed evidence | Used to shape the search subjects and the bridge-list draft |

## 2. Overall shape

```
 DAILY JOB (fixed time)                              WEEKLY JOB
 ----------------------                              ----------
 journals  \                                         read this week's rows
 researchers -> discover -> identify/dedupe ->       -> re-check retractions
 topics    /     (PubMed, Europe PMC,    flag +      -> select + group (sections)
                  Crossref, OpenAlex,    enrich      -> build DIGITAL (HTML, links)
                  CT.gov, preprints)     |           -> build PRINT (PDF: full text,
                                         v                 QR + DOI line per paper)
                                   +-----------+     -> Gmail draft (body + PDF)
                                   | DATABASE  | <-------------------+
                                   +-----------+
```

The database is the only link between the two jobs, so the weekly job can be re-run or re-built for any past week without searching again.

## 3. Part 1: the daily job

**Schedule [Open]:** the time of day is not chosen. Because of the 3-day overlap, any time works and a missed day is caught by the next run.

**Stages per run [Proposal]:**
1. **Discover.** For each path, query the sources with the window `today - 3 days .. today`:
   * *Journals path:* PubMed by ISSN-based journal query with the `migraine` gate; Crossref `/journals/{issn}/works` as a second route; publisher feeds only where one was reachable (JAMA Neurology and Nutrients have none we could reach, so they rely on PubMed and Crossref).
   * *Researchers path:* PubMed `Surname Initials[Author]` (the sheet's `"First M. Last"[Author]` format returned 0), OpenAlex `author.id` with the owner-confirmed IDs, filtered to the window and to migraine.
   * *Topics path:* the Search Subjects terms against PubMed and Europe PMC; preprints through Europe PMC `SRC:PPR` (one request instead of paging bioRxiv/medRxiv); ClinicalTrials.gov v2 for trials.
   * Use `edat` (entry date) for PubMed freshness, not publication date, and record both.
2. **Identify and dedupe.** Key on DOI, then PMID, then title/author/year match. A preprint and its published version have different DOIs; link them through the preprint's "published" information where a source provides it (not yet tested for bioRxiv/medRxiv).
3. **Gate.** Keep a paper only if `migraine` is in the title or abstract; Track B papers without that word are admitted only through the approved bridge list and marked "exploratory". Generic terms alone never admit a paper.
4. **Enrich.** Abstract and metadata; free-copy links (OpenAlex/Unpaywall); licence; full text where permitted (section 5).
5. **Flag.** Retracted (PubMed type, Crossref `updated-by`, OpenAlex `is_retracted`: any one is enough); retraction notice (attach, do not list); preprint (non-peer-reviewed); full-text status.
6. **Store and log.** Write papers, hits and a run log (what was queried, counts, errors).

**Failure handling [Proposal]:** each source runs in isolation, so one blocked or rate-limited source does not stop the run; failures are logged and the next day's overlap recovers most gaps. A budget guard watches the OpenAlex daily allowance (10,000 credits, about 10 per simple request).

**Filter rules confirmed by testing on 2026-10-30 (details in `Window_filter_tests.md`) [Proposal]:**
* **Date field per source:** PubMed `edat`, Europe PMC `FIRST_IDATE`, Crossref `from-created-date`, ClinicalTrials.gov `StudyFirstPostDate`. Do not use publication-date filters (PubMed `pdat` found 37% of new records in W0, Europe PMC `FIRST_PDATE` 33%) or Crossref `from-index-date` (18 times too many). OpenAlex created/updated filters need a paid plan, so OpenAlex is a supplement (publication date, wider lookback, DOI dedupe).
* **Union the sources.** In the test window any single source found at most 53% of the distinct new papers; Crossref added 37 papers PubMed does not index (some from low-profile journals, so a source-quality screen is needed).
* **Gate:** `migrain*` (PubMed, Europe PMC) rather than `migraine`, which misses plurals (about 3%). Keep the gate on the journal and author paths: ungated they bring 248 papers (journals) and many same-name authors in four days.
* **English rule applied after retrieval,** because Europe PMC `LANG:eng` silently drops records with no language value (about 5%).
* **Flag retracted papers before dropping noise types** (letters, comments, errata); retraction notices are `Retraction Notice` in PubMed.
* **Query every ISSN of a journal** on Crossref (print and online can differ).
* **Silent-zero alarm:** a filter that normally returns results but returns none should raise an alert (ClinicalTrials.gov returned a silent 0 for an unsupported field).
* **Weekly cross-check: decided, none for now (owner, 2026-10-31).** The next-day re-run of the 30 October snapshot found no back-dated records (0 of 289 across 23 to 29 October in PubMed, Europe PMC and Crossref; `Window_filter_tests.md` section 9). Keep the daily 3-day overlap only; OpenAlex's 14-day lookback is a second route. Re-check after a few weeks of daily runs using the database's own data (papers whose source entry date is older than the run window when first stored).
* **Run tolerance:** a paper is lost only if four consecutive daily runs are missed (derived, not tested).

**Rate and budget facts (tested):** PubMed 10 requests/s with the key; Crossref 1/s on `/works` without registration; OpenAlex key gives $1/day. Recent volume was about 421 records in 30 days for an unfielded `migraine` query, roughly 14 a day, so volume is small.

## 4. Database [Proposal]

Postgres with Alembic migrations, as in the existing repo. Core tables:

| Table | Holds |
|---|---|
| `papers` | One row per paper: DOI, PMID, PMCID, title, abstract, journal, ISSN, language, type, dates (published, indexed), track (A/B), flags (preprint, retracted, exploratory) |
| `paper_hits` | Every time a paper was found: path (journal / researcher / topic), source, exact query, match reason, found-at |
| `paper_links` | Article, PDF, DOI, PMC links with source and whether free |
| `paper_fulltext` | Status (`full_text` / `abstract_and_links_only`), format (JATS XML / PDF text), licence, source URL, fetched-at, the text or a pointer to it |
| `retraction_checks` | Per paper: which source flagged it, when checked |
| `researchers`, `journals` | The 30 confirmed researchers with IDs and aliases; the 10 journals with ISSNs and aliases |
| `bridge_terms` | The approved Track B bridge list |
| `runs`, `source_run_log` | Daily run history and per-source results, for monitoring |
| `issues`, `issue_items` | Each weekly edition and which papers were sent in which section |

`pgvector`/embeddings from the existing repo are **not needed** by the current requirements **[Open]**: keep them only if semantic search is wanted.

## 5. Full text for the print edition

Order of preference **[Proposal]**:
1. **Europe PMC full-text XML** (PMC items): tested 200 OK, 110-140 KB structured text. Cleanest source.
   * **Springer Nature Open Access API** (key in `.env`): for Springer/BMC papers that are not in PMC it returned full-text JATS with the licence for 14 of 28 sampled DOIs; the others are not open access. Same cleanliness as PMC XML. Free-tier terms say non-commercial use (to be judged by the owner/client).
   * **Elsevier and Wiley: dropped by the owner 2026-10-30; no keys will be requested.** Papers from Elsevier, Wiley and SAGE that scripts cannot reach stay abstract + QR + links. Background (not tested with keys): Elsevier's API returns metadata only without a key and needs an institutional entitlement for paywalled papers (about 4 of 20 sampled Elsevier papers are open access); Wiley's TDM token returns PDFs and its terms point to library agreements. Expected yield is low (Elsevier) to uncertain (Wiley). See `Discovery_API_check.md` section 2.12.
2. **Free PDF** from OpenAlex/Unpaywall, converted with `pdftotext`: tested 13 of 30 downloaded and gave text; Wiley blocked 14 of 14. Reading order, tables and figures not verified.
3. **Nothing reachable or not permitted:** abstract and links only, labelled as such.

Licence handling **[Open]**: in the 30-day test, 277 papers were open access in PMC (CC BY 210, CC BY-NC 33, CC BY-NC-ND 32, CC BY-NC-SA 1). CC BY can be reproduced with credit. Whether printing CC BY-NC / -ND papers in full is acceptable is a legal question for the client. Until answered, the safe default is full text for CC BY only and abstract plus QR for the rest. Each printed paper should carry its licence and attribution line.

Licence per paper **[Proposal]**: take it from Crossref's licence entry (checked against OpenAlex/Unpaywall). In a test of 140 papers not in PMC open access, 59 had a CC licence on the published version (CC BY 30, CC BY-NC 23, CC BY-NC-ND 6). Publisher routes (tested only through PDF downloads): Springer, Frontiers, Dove Press, BJGP Open and Spandidos served PDFs to a script; Wiley blocked every attempt; Elsevier, Wolters Kluwer (Neurology) and Thieme were not tested. Publisher APIs need keys or accounts and were not tested.

Coverage test: about 86% of recent papers have some free copy; about 12% look closed. A subscription would give access but not republication rights **[Owner: no subscriptions for now]**.

## 6. Part 2: the weekly job

1. Read the week's rows; re-check retractions on items sent in recent weeks **[Proposal]** (how many weeks is **[Open]**).
2. Select and group **[Open: how many papers per print edition and who selects]**. Proposed sections: Direct migraine digest; Exploratory (Track B); Preprints (labelled non-peer-reviewed); Retracted (separate, flagged); Trials.
3. Build the **digital** edition: HTML and plain text with headlines, summaries and links. Reuse the existing Jinja2 renderer with a new template.
4. Build the **print** edition as a PDF: full text where section 5 allows it, otherwise abstract and links; one QR per paper (article page) with a DOI line beneath (source URL if no DOI); retracted section included. **[Proposal]** add a second QR for a free PDF where one exists. **[Open]** large print or high contrast, and how figures and tables appear.
5. Create the **Gmail draft** with the digital edition as the body and the PDF as an attachment. The existing draft builder has no attachment support, so that is new work. A thick PDF could approach Gmail's attachment size limit (about 25 MB, from general knowledge, not tested): another reason to cap papers per edition.

## 7. What carries over from the existing newsletter repo

Facts from the cloned repo (about 3,450 lines of Python under `src/`):
* **Reuse as is:** the hexagonal layout (domain / application / infrastructure with ports in `ports.py`), config handling, Alembic migrations, Docker + Railway deployment, the idempotent Gmail draft publisher (it looks up an existing draft by an issue-key header), Jinja2 rendering, and the LLM categoriser for summaries and grouping.
* **Replace or extend:** the intake. The old agent reads Google Scholar alert emails from a Gmail label; the new one calls the discovery APIs. The old run is one weekly cron (`30 14 * * 4`, Thursday 14:30 UTC = 20:00 IST); the new design needs a daily cron and a weekly cron. The draft builder needs an attachment. The old schema has `research_items`, `issues`, `issue_items`, `embeddings`; the new tables in section 4 supersede most of it.
* **[Open]** whether the Scholar-alert intake stays as an extra source. Scholar has no API, so alerts are the only legitimate way in.
* **[Open]** how to run two schedules on Railway (two cron services or one service with two commands); not checked.

## 8. Build order [Proposal]

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Project skeleton, database schema, config, run log | Migrations apply; a dry run logs an empty day |
| 1 | Core discovery: PubMed + Europe PMC, three paths, dedupe, gate | Replaying a past week finds the known papers once each |
| 2 | Remaining sources: Crossref, OpenAlex, ClinicalTrials.gov, preprints | Each source isolated; a forced failure does not stop the run |
| 3 | Flags and enrichment: retraction, preprint, links, licence, full text | The 18-paper retraction set is flagged; full-text status is correct on a sample |
| 4 | Weekly digital edition and Gmail draft | A real week produces a draft with correct sections |
| 5 | Print edition: PDF, full text, QR + DOI line, retracted section | A printed sample scans and is readable (needs the reader's input) |
| 6 | Scheduling, monitoring, alerting | Both jobs run unattended for a week |

## 9. Decisions so far

| Date | Decision | By |
|---|---|---|
| 2026-10-30 | PubMed is the Cochrane stand-in; Track B journals stay in the journal path; 30 researcher profiles confirmed | Owner |
| 2026-10-30 | Curated mechanistic bridge (narrow, client-approved list; `migraine` stays the default gate) | Owner delegated, my call, provisional |
| 2026-10-30 | Include preprints from extra servers, all labelled non-peer-reviewed | Owner |
| 2026-10-30 | Retracted papers kept and flagged in a separate section; notice handling as in section 1 | Owner |
| 2026-10-30 | English only; 3-day overlap; existing Gmail-draft route | Owner |
| 2026-10-30 | Two-part pipeline; print edition with full text; abstract + links fallback; QR on every paper with DOI line | Owner |
| 2026-10-30 | No subscriptions for now | Owner |
| 2026-10-30 | Springer Nature key kept; Wiley TDM token and Elsevier key dropped | Owner |
| 2026-10-30 | Two pipelines = application side (serves users from the DB) + newsletter | Owner |
| 2026-10-30 | Multi-user scaling (sign-up, catalogue, other fields) is **v2.0**; v1 serves Curt only but keeps the architecture flexible for other profiles | Owner |
| 2026-10-30 | Application side (read-only access for on-the-go use) is in **v1.0**; build skeleton, schema, discovery and data ingestion first | Owner |
| 2026-10-30 | Newsletter and app select papers by the date **our database first stored them** (`papers.first_seen_at`), not by journal issue or publication date or the source's own entry date. The publication date is still shown to readers (month only when that is all the source gives). Each edition records which papers it sent so none repeats. | Owner |
| 2026-10-31 | No weekly safety-net search for back-dated records for now; revisit after a few weeks of real runs | Owner |
| 2026-10-31 | Papers published long before we first found them (late-indexed, or surfaced by a lookback) do not appear in the week they surface: the newsletter leaves out papers published more than `newsletter.freshness.max_publication_age_days` (default 7) before our database first stored them. Selection is still by our stored date; this is an extra filter. They stay in the database and the app. | Owner |
| 2026-10-31 | **Licences do not limit printing**: any full text we hold may go in the print edition, whatever its licence or whether it came through paid access (supersedes the CC BY only default and client question 1). Licence and rights are still recorded; the stricter rules stay available as a profile option. Papers with printable full text in hand rank higher (weight 3, tunable). | Owner |
| 2026-10-31 | **Papers from watched journals and researchers are chosen first** when a cap bites (`newsletter.caps.priority_to_watched`, default on), and are the last trimmed by the size limit. | Owner |
| 2026-10-30 | A paper already sent in an earlier edition is **never resent**, even if its full text is found later. Full text found before that week's edition is built goes into that edition. Late full text stays in the database and the app. | Owner |

## 10. Open questions

**For the owner / client (ready to send):**
1. Is printing papers licensed CC BY-NC or CC BY-NC-ND acceptable? About one in four open-access papers carries one of those restrictions.
2. How many papers go in each print edition, and does the agent's ranking or a person choose them?
3. Should the print edition be large print or high contrast? Which font size?
4. What does "abstract-only" in rule 8 mean? (Our reading: only the abstract is readable. Please confirm.)
5. What time should the daily job run?
6. How many past weeks of sent papers should be re-checked for retractions?
7. Is the Scholar-alert email intake still wanted as an extra source?
8. Can the client supply feed URLs for JAMA Neurology and Nutrients?
9. Who approves the Track B bridge list, and can we draft a starting list for them?
10. Should figures and tables appear in the print edition?
11. Crossref surfaces new migraine papers from journals PubMed does not index (some headache-specific, some low-profile). Include them, and with what quality screen?

## 11. Risks

* **Licences:** printing full text without clear permission. Owner decision 2026-10-31: licences do not limit printing; the responsibility for that choice sits with the owner and client. The licence and rights of every text stay recorded so the policy can be tightened later (`fulltext.print_text_regardless_of_licence`).
* **Blocked publishers:** Wiley blocks scripts; a PDF route will fail for some. Mitigation: prefer PMC XML, fall back to links.
* **PDF text quality:** reading order and tables unverified. Mitigation: test on a sample before relying on it.
* **Retraction coverage gaps:** no single source is complete. Mitigation: any of three sources flags it; re-check sent items.
* **Print volume:** hundreds of pages if uncapped. Mitigation: cap per edition.
* **OpenAlex budget:** metered at $1/day. Mitigation: budget guard and batch lookups (50 DOIs per request works).
* **Untested pieces:** preprint-to-published linking, PMC embargo delay, Railway multi-schedule setup, QR library choice (not chosen or tested).

## 12. Two pipelines and multi-user scaling [Owner: scaling is v2.0; v1 stays flexible]

**Scope decision (owner, 2026-10-30): multi-user scaling is version 2.0. Version 1 serves Curt only, but the architecture must stay flexible for other profiles.** So v1 builds the cheap flexibility rules in 12.3 and the v1 columns in 12.4; everything marked **v2.0** (sign-up, catalogue, onboarding, per-user delivery, second field) is design notes only, not built and not scheduled.

**Plan only. The user-facing parts are not built now; the foundations below are, because they are cheap today and costly to retrofit.**

### 12.1 Reading of the two pipelines

```
 COLLECTION (daily, shared)  ->  DATABASE  ->  A. APPLICATION SIDE (on demand, serves users)
 searches sources, dedupes,                    B. NEWSLETTER (weekly, per profile)
 flags, enriches
```

* **[Open, please confirm]** My reading is that collection stays a daily backend job that fills the database, and both the application side and the newsletter only *read* from it. If you meant collection to be part of the application side, the tables below do not change.
* **Application side:** an API that answers "what is new for me?" from the database, with filters (date, field, section, retracted, full-text available) and the paper detail (abstract, links, licence, full-text status). It never queries PubMed and other sources live.
* **Newsletter:** the weekly job from section 6, now run per profile instead of once.

### 12.2 What a user chooses

| Choice | Offered as | Becomes |
|---|---|---|
| Field | A catalogue of fields we support (migraine today; L-theanine next as a test case) | A **field definition**: gate terms, synonym list, bridge terms, track rules |
| Journals | Our validated list for that field | Rows in `profile_journals` |
| Researchers | Our validated list with confirmed IDs | Rows in `profile_researchers` |
| Search topics | The field's offered subjects | Rows in `profile_topics` |

Anything a user asks for outside the catalogue is an **onboarding request**: we validate it (the same work done by hand for migraine: ISSNs, researcher IDs, term checks, a test search) and add it to the catalogue only after review. This keeps quality controlled and makes "process their request and tune the pipeline" a visible workflow rather than ad-hoc work.

### 12.3 Flexibility rules that v1 follows (cheap now, costly to retrofit)

1. **Nothing field-specific in code (v1).** `migrain*`, the Track A/B logic, the bridge list and the 12 search subjects move into a `fields` configuration. Curt migraine becomes tenant/profile number 1.
2. **One shared paper store, per-profile matching (v1: one profile).** A paper is stored once (DOI/PMID dedupe). A `profile_matches` table says which profiles it matches and why. Matching is re-evaluated when a profile changes, without searching sources again.
3. **Collect from a stored list of searches, not hard-coded queries (v1); dedupe across profiles is v2.0.** Collection runs the union of the *distinct* queries (field + journals + researchers + topics) across all active profiles, so 100 users with the same choices cost the same API budget as one. This matters because OpenAlex is metered ($1/day) and PubMed allows 10 requests/s.
4. **Every query remembers who needs it (v1: records the profile id).** `paper_hits` keeps the exact query; a `search_specs` table links queries to the profiles that use them, so removing a profile's choice retires queries nobody needs.
5. **Newsletter keyed to a profile (v1: one).** `issues` gets a profile reference; the idempotency key becomes profile + week.
6. **Time zone and schedule live on the profile (v2.0 use)** (delivery day/time), while collection stays on one clock.

### 12.4 Tables: what v1 builds and what waits for v2.0

* **v1 builds:** a `fields` row (migraine: gate terms, synonyms, bridge terms, track rules, loaded from configuration), one `profiles` row (Curt), and `profile_id` on `search_specs`, `profile_matches` and `issues`. The existing `journals`, `researchers` and `bridge_terms` tables from section 4 stay as they are and hold the Curt data.
* **v2.0 builds:** `users`, the catalogue tables, `profile_*` selection tables and `onboarding_requests`. These are listed so v1 does not block them; v1 does not create them.

Full list for reference:

| Table | Holds |
|---|---|
| `users` | Account, contact, status |
| `fields` | Field definition: name, gate terms, synonyms, bridge terms, track rules, validation status |
| `catalog_journals`, `catalog_researchers`, `catalog_topics` | The offered options per field, each with its validation evidence (replaces plain `journals`/`researchers`/`bridge_terms` for non-Curt fields; the Curt data is loaded as the first catalogue) |
| `profiles` | One per user per field: delivery settings, print or digital, language (English for now) |
| `profile_journals`, `profile_researchers`, `profile_topics` | What the user chose |
| `profile_matches` | Paper-to-profile matches with the reason |
| `search_specs` | Distinct queries and which profiles use them |
| `onboarding_requests` | Free-text requests, status (new / in validation / added / declined), reviewer notes |

### 12.5 Things that change once there are many users

* **Delivery.** The Gmail-draft route works for one owner who reviews and sends drafts. For many users it needs real sending (an email service) and per-user bounce/unsubscribe handling. **[Open]** keep drafts for Curt only, or move to sending when the second user arrives.
* **Print edition.** Printing to a specific reader is a manual/offline step today. For other users, decide whether print is an Curt-only feature or a generated PDF they print themselves.
* **Licences and terms.** Section 5's licence caution gets sharper: serving full text to many users is redistribution at scale. The Springer Nature free tier is documented as non-commercial, and OpenAlex, NCBI and Crossref have their own usage terms. **[Open]** before any paid or public rollout, review each source's terms for commercial use. Until then, full text only for CC BY and links for the rest.
* **A new field is a validation project.** L-theanine is a compound, not a condition, so its gate terms, synonyms, journals and researchers differ, and volume is likely far lower than migraine (not measured). The filter findings in `Window_filter_tests.md` (entry-date fields, union of sources, silent-zero alarm) are field-independent; the numbers (14 papers a day, 53% single-source ceiling) are migraine-only and must be re-measured per field.
* **Privacy.** Profiles hold email and interests; the reader's health situation is context for the print edition, not stored data. Keep the store minimal and decide retention before sign-up opens.
* **Abuse and limits.** Per-user caps on profile size (journals, researchers, topics) to protect API budget.

### 12.6 Effect on the build order

| Phase | Change |
|---|---|
| 0 | Schema carries `field` and `profile_id` from day one; seed one field (migraine) and one profile (Curt). No users table, no sign-up. |
| 1-2 | Collection reads its queries from `search_specs`, not hard-coded constants. |
| 3 | Gate and flags read the field definition. |
| 4-5 | Newsletter built per profile; the single Curt profile is the only one run. |
| v2.0 | Sign-up, field and source selection, request workflow, per-user delivery |
| v2.0 | Second field (L-theanine) as the proof that nothing is migraine-specific |
| 7 (v1) | Application-side API for on-the-go use (read-only from the DB) **[Owner: v1.0, 2026-10-30]** |

### 12.7 Open questions (deferred to v2.0 planning, except 12)

12. What form does the application side take: a web app, a mobile app or only an API at first? Who are the on-the-go users (the Curt reader, clinicians, the public)?
13. Do sign-ups pay, and is the service public or invite-only? (This decides the source-terms review in 12.5.)
14. Does each user get their own newsletter schedule, or one shared weekly edition per field?
15. Is the catalogue closed (only what we validated) or may users add their own journals and researchers for us to validate?
16. Is L-theanine the second field for sure, and who supplies its journals, researchers and topics?

## 13. Build status (v1.0), 2026-10-30

**Built and tested:** phase 0 (skeleton, schema, config, run log), phase 1 (PubMed and Europe PMC, three paths, dedupe, gate) and phase 2 (Crossref, OpenAlex, ClinicalTrials.gov, preprints), plus the retraction, licence and free-link parts of phase 3. Code is in `src/research_agent/`, documented in `README.md`. Not built yet: full-text retrieval, the weekly newsletter job, the read-only application API, scheduling, publisher feeds.

**Tests:** 100 offline tests pass (95% line coverage) using real API responses saved in `tests/fixtures`; one live end-to-end test passes against the real services.

**Runs on the real services (window 2026-10-27..30, 94 searches, about 50 seconds):**
* First run into an empty database stored 233 papers (217 articles, 13 preprints, 3 trials); a repeat of the same window stored 0 new (idempotent).
* Replaying five consecutive days into an empty database: day 1 stored 229; days 2 to 5 added 27, 23, 19 and 18. So steady state is roughly 20 new papers a day across all sources.
* Of the 233 papers, 158 came from one source only (129 of them OpenAlex), 51 from two, 12 from three and 12 from all four discovery indexes (PubMed, Europe PMC, Crossref, OpenAlex). Single-source papers confirm the earlier finding that no source alone is enough.
* The journal and researcher paths added nothing beyond the topic path in PubMed (the gated topic query is a superset), as `Window_filter_tests.md` predicted. They stay because they label why a paper matters and cover indexing gaps.

**Retraction detection (live, 19 PubMed-flagged retracted migraine papers):** PubMed type 19 of 19, OpenAlex 19 of 19, Crossref 16 of 19; any one source is enough, so all 19 are flagged. All 8 PubMed retraction notices parsed with their target paper.

**Facts found while building (new, all observed live):**
* PubMed `esearch` always returns an `errorlist` object (empty lists); only a non-empty `fieldsnotfound` is an error. Treating the object as an error broke every journal search on the first run.
* Crossref has no record for two online ISSNs (Lancet Neurology 1474-4465, JAMA Neurology 2168-6157): `journals/{issn}/works` returns 404. Those are skipped, not errors; the print ISSNs work.
* A retracted article's own Crossref record can list a retraction that points at itself. It must not be read as a retraction notice.
* One real PubMed "Retraction notice" is typed `erratum` by Crossref. Machine-readable types are therefore not enough; a conservative title check (notice and withdrawn/retracted prefixes) is an extra signal.
* Crossref `select` rejects `subtype` and `language`; Crossref's response header says 3 requests per second; the adapter paces itself at 1 per second.
* OpenAlex reports the credits left in the `x-ratelimit-remaining` header (a simple request costs about 10); the credit guard reads that.
* OpenAlex returns many repository deposits (Zenodo 21, Open Science Framework 7, Figshare 4) and book chapters among its migraine results; see open decision below.

**Decisions made in the build (change on request):**
* Subjects are not searched separately. Every search carries the gate term, so a per-subject query is a subset of the topic query. Subjects classify papers locally.
* Track is a proposal: direct (A) evidence wins; B only when every specific signal is mechanism-only; the gate word alone does not count. Needs client review (plain words such as "sleep" and "mood" currently count as direct evidence).
* A record whose abstract is missing and whose title lacks the gate word is "unknown": abstract lookup via OpenAlex and Europe PMC; if still unknown it is stored but not treated as relevant.
* Non-English papers are stored and marked `other`; records with no language value are kept (`unknown`).
* Researcher name hits from PubMed carry lower confidence than OpenAlex-ID hits; three ambiguous names (Wang SJ, Charles A, Hoffmann J) never decide the track.
* The database carries `field` and `profile_id` throughout (decision of 2026-10-30); only Curt is seeded.

**Decided after the build (owner, 2026-10-30):**
* Repository deposits (Zenodo, OSF, Figshare, institutional repositories), datasets and book chapters are **stored and flagged, and kept out of digests by default**. Each paper has a `content_type` (`journal_article`, `preprint`, `trial`, `repository_deposit`, `dataset`, `book_chapter`) set from the DOI prefix, repository name and source type; the profile's `digest.exclude_content_types` lists what is hidden; `research-agent papers --all-types` shows everything and `research-agent reclassify` re-applies the rules. In the dev database 28 papers were repository deposits, 7 datasets and 16 book chapters out of 233.
* Contact address for NCBI/Crossref: keep the current one until the daily job goes live (`CONTACT_EMAIL`).
* Supabase: not yet. The schema is written for Postgres (row level security on every table) but has only been tested on SQLite; applying it to Supabase needs the Session pooler connection string.

**Still open from the build:** a quality screen for low-profile journals that only Crossref or OpenAlex index (question 11 in section 10); the track rule needs client review.

## 14. Provider tiers and full-text retrieval (owner direction 2026-10-30, built)

**Requirement (owner):** ingestion and full-text retrieval must not be built around free-tier keys only. If premium keys are provided now or later, the code routes to the more capable retrieval.

**Design.** Every external service is a *provider* with a tier (free / premium), a set of capabilities and the credentials it needs (`domain/capabilities.py`, `infrastructure/registry.py`). A credential in `.env` switches a provider on or moves it up a tier; the router picks among active providers. `research-agent providers` prints the state and what each upgrade would change.

| Area | Free route (built, tested live) | Premium route (built, mocked tests only) |
|---|---|---|
| PubMed | 3 requests/s | `NCBI_API_KEY`: 10 requests/s (live) |
| OpenAlex discovery | publication-date lookback, metered | `OPENALEX_PLAN=premium`: exact created-date filter; refused keys fall back automatically |
| Crossref | public pool, 1 request/s | `CROSSREF_PLUS_TOKEN`: header sent, faster pacing |
| Full text: open | Europe PMC XML, Springer Nature open access (free key), free PDF copies via `pdftotext` | |
| Full text: paid | | Elsevier ScienceDirect (plain text), Wiley TDM (PDF) |

**Rights are separate from access.** Each stored text records provider, tier, rights (`open` / `entitled` / `unknown`), normalised licence and `print_allowed`. Open text is printable only for the profile's `fulltext.print_licences` (default CC BY, CC0); text obtained through a paid entitlement is stored but not printable unless the profile allows it (`allow_entitled_in_print`, default false). Paid access gives the right to read; republication needs the publisher's licence. ~~Client decision still open (section 10, question 1).~~ Superseded 2026-10-31: the owner decided licences do not limit printing; see the decisions table.

**Routing behaviour (tested).** Providers run in priority order (open structured XML first, then paid text, then PDFs; `FULLTEXT_ORDER` overrides); first success wins; every attempt is logged in `fulltext_attempts`; a provider that could not help is skipped for `retry_days` (7); errors are retried next run; a provider added later is tried on papers the others missed; crashing providers and per-provider quotas are handled; credentials are never sent to a different host after a redirect and never appear in logs.

**Live result with free providers only (233 stored papers, 175 eligible after excluding deposits, datasets, book chapters and trials):** full text for 68 (39%): Europe PMC XML 30, free PDF copies 31, Springer open access 7. 51 of the 68 are CC BY and printable under the default policy; the rest carry CC BY-NC, -ND or -SA licences or none. 107 papers have only abstract and links. The 4-day-old papers are mostly not yet in PubMed Central: only 32 of 233 had a PMC ID, and a PMC-ID lookup for every new paper found no more. So the free routes cover about 4 in 10 recent papers; the rest are what a Wiley, Elsevier or other entitlement would add (not measured; needs a key).

**Quality notes.** PMC/Springer XML converts cleanly (sections, figure and table captions, references omitted; median about 6,000 words). PDF text keeps the journal's front matter (header lines, correspondence block) and its reading order is not checked for two-column layouts or tables; the print layout stage must clean this. HTTP 500 from Europe PMC on two PMC IDs was retried next run by design.

**Not verified live:** Elsevier, Wiley, Crossref Plus and OpenAlex created-date behaviour (documented endpoints and headers, mocked tests only); Springer free-tier limits.

**Later arrivals and a wider test (2026-10-30).**
* *Re-check.* Papers that had no PMC ID or free copy when first stored are looked up again (Europe PMC for the PMC ID, OpenAlex for free-copy links) at most once per `fulltext.retry_days` (7), for `fulltext.recheck_days` (90) after first entry, on every `fulltext` pass; papers that then have a route are fetched in the same pass. Each pass is logged as a `fulltext` run with the count of new PMC IDs, so the real rate of late arrival will be visible after a few weeks of daily runs. It was not measurable on day one: a lookup right after collection finds nothing new (90 re-checked, 0 new).
* *PMC ID share by age* (PubMed and Europe PMC papers only; today's lookup; samples of 35 to 108, not a steady trend): 0 to 3 days old 51%, 2 weeks 69%, 1 month 47%, 2 months 74%. The 14% quoted earlier was for all sources including repository uploads.
* *Free providers over 246 eligible papers from those windows:* text for 127 (52%), 89 printable under the default policy. Europe PMC XML 108, Springer open access 10, free PDF copies 9. 119 abstract and links only.
* *Europe PMC full-text errors.* 42 requests ended in HTTP 500 for 40 papers that do have a PMC ID. Retried a few minutes later: 38 still fail, 2 now work. NCBI's own PMC service (`efetch db=pmc`) returned body text for 4 of the 40 (2 of real length, about 4,200 to 4,500 words, no licence statement), so a NCBI fallback would add about 2 papers in 246 with unknown printing rights; not built. To avoid hammering a failing endpoint, repeated errors on the same paper and provider now wait 1, 2, 4 ... days (never beyond the retry window).
* *Bug fixes found by this work:* the re-check needs its own timestamp (the fetch pass was resetting the shared one, so the weekly re-check would have fired once); PubMed returns Bookshelf chapters (StatPearls) as `PubmedBookArticle`, which the parser silently dropped and reported as a truncated search; they are now stored as book chapters.

**PMC lag is now measurable (2026-10-30/31).** `papers.pmcid_found_at` records when the database first held a PMC ID for a paper; the lag is that minus `first_seen_at`. `research-agent pmc-lag` prints how many papers got their ID the same day, within 7, 30 and 90 days, and how old the still-missing ones are. Rows stored before the column existed were backfilled with `first_seen_at`, so they count as "same day" whatever really happened; the measurement is only meaningful for papers first stored from now on. First meaningful reading: after a few weeks of daily `collect` + `fulltext` runs.

## 15. Weekly digital edition (built 2026-10-31; print edition not started)

**Scope (owner):** digital edition only. The printable PDF is not to be worked on until the client supplies a template.

**Built and tested (199 offline tests):** `newsletter build | list | show | publish | mark-sent`. Selection by `papers.first_seen_at`; sections (retractions first, direct research grouped by subject, related mechanisms, preprints, trials); watched-journal and watched-researcher labels (ambiguous name matches are never advertised); extractive summaries; HTML (large serif type, high contrast, text labels not colour alone, tested in a browser) and plain text; issues and items stored with a snapshot; `UNIQUE(profile, paper, role)` so a paper cannot be carried twice; weekly retraction re-check of recently sent papers; idempotent builds; file draft publisher (works now) and Gmail draft publisher (ported from the earlier agent; narrower `gmail.compose` scope, drafts only; tested with a fake Gmail service, **not run against a real account** because no OAuth credentials exist).

**Run on stored data (178 items from 233 stored papers, week ending 31 Oct):** 164 direct, 2 mechanisms, 9 preprints, 3 trials, no retractions that week. The HTML is 160 KB. Gmail clips messages over about 100 KB, so an edition this size would be cut off in the reader's inbox; the build now warns. The test data is a 7-day seed from three overlapping searches; steady-state volume is probably 100 to 150 items a week, still near the limit. This is the same question as client question 2 (how many papers per edition and who chooses): a cap, a web-hosted full edition with a short email, or a longer-form attachment.

**Findings while building:** abstracts from Europe PMC, OpenAlex and Crossref carry section labels without colons ("Background ... Methods ... Conclusion ...") so summaries had to recognise both styles; journal names differ by index ("Cephalalgia : an international journal of headache"), so a watched journal is always shown under its configured name; preprints can have no journal.

**Not done / open:** the print PDF; whether to use a language model for summaries (the old agent did; needs an Anthropic key and a decision); recipients (empty, so drafts have no To line); how the first real edition's window should start in production; scheduling; the application API.

**Summaries through the Groq API (owner direction 2026-10-31, built).** The earlier newsletter agent used the Groq API through an OpenAI-compatible chat endpoint (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`); the new code uses the same settings, request shape (JSON mode with a fallback when a model rejects it), pacing (2.1 s between requests) and retry-with-`Retry-After` behaviour. The model name is not stored in the old repository (it lived in the deployed environment), so it has to be supplied; nothing has been run against Groq yet. Additions for medical content: the paper's title and abstract are passed as untrusted data; the prompt allows only what the abstract states; **every number in a summary must occur in the abstract** (the check that catches invented figures); links and over-long answers are rejected; one retry; then the abstract extract is used instead. Good summaries are cached in `paper_summaries` keyed by paper, abstract, model and prompt version, so rebuilding an edition costs no model calls and failed ones are retried next build. The summary step runs before the edition's database transaction, so no transaction is held open during slow calls. Each item's snapshot records `summary_source`; the footer discloses model-written summaries. A cap (`LLM_MAX_SUMMARIES_PER_RUN`, 300) bounds one build: at one request per 2.1 s a 178-item edition takes about 6 to 7 minutes the first time.

**Security fix found while testing:** when a provider's error message echoes an API key back ("bad key ..."), pattern-based scrubbing missed it. The HTTP client now remembers every credential it sends (headers and parameters) and removes those exact values from any error text; this covers every provider.

**Live Groq test (2026-10-31).** The key can use 11 models (no Llama models among them): `qwen/qwen3.8-27b`, `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `openai/gpt-oss-safeguard-20b`, `allam-2-7b`, two prompt-guard models, two text-to-speech models and two Whisper models. The configured model is `qwen/qwen3.8-27b`, which Groq lists as a **preview** model that may be discontinued at short notice. Same 8 stored abstracts through three models: qwen3.8-27b 8/8 passed the checks (about 41 words, about 2 s a request, JSON mode accepted); `openai/gpt-oss-20b` and `openai/gpt-oss-120b` 8/8 only with `LLM_REASONING_EFFORT=low` (about 51 words, wordier: they add standard deviations and p-values, and one cut off mid-bracket). The first attempt at the gpt-oss models failed 7/8 because they are reasoning models and the 400-token output cap I had just added was spent thinking; the client now raises the budget itself when an answer is cut off, accepts JSON wrapped in other text, and sends `reasoning_effort` when configured. Production fallback if the preview model is withdrawn: `openai/gpt-oss-20b` with `LLM_REASONING_EFFORT=low` (a production model on Groq's page, $0.075 input / $0.30 output per 1M tokens).

**Rate limits seen in Groq's response headers for this key:** 8,000 tokens per minute and 1,000 requests, far below the 250K TPM in the published table, so the key is on a restrictive tier. A summary request costs about 800 to 900 tokens, so the free limit allows roughly 9 requests a minute. The client now reads the remaining-token header and waits for the reset instead of sending requests that would be refused.

**A check that was too strict, found live:** an abstract that said "seventeen reviews" produced a correct "17"; the number check now understands numbers written as words.

**Full edition with `qwen/qwen3.8-27b` (178 items, copy of the dev database, 2026-10-31):** 161 summaries written by the model, 15 papers have no abstract (nothing to summarise), 2 failed the checks and used the abstract extract. The build took about 15 minutes: 167 requests, no refusals, 11 pauses of about 50 s while the client waited for the token budget to reset. Word counts: median 43, range 20 to 67. One rejected summary stated a number (66) that the abstract does not contain, which is the check doing its job. A scan for hype or advice wording ("cure", "proven", "should take", "breakthrough") found nothing; the one hit was the phrase "all-cause". Cached: a second build of the same edition makes no model calls. Practical consequence: the first build of a large edition needs about 15 to 25 minutes on this key's 8,000 tokens a minute, so the weekly job must be scheduled with that in mind or the plan upgraded.

**Edition size caps (owner, 2026-10-31: "cap it and put the capping settings in the config that can be tuned later").** `newsletter.caps` in the profile (`config/profiles/curt.yaml`): `per_section` (defaults 30 direct, 15 mechanisms, 10 preprints, 10 trials; missing or null = uncapped; retractions uncapped by default), `total` (optional, never removes a retraction) and `max_html_kb` (default 95: after rendering, the lowest-ranked non-retraction papers are trimmed until the HTML fits, because Gmail clips near 100 KB; null switches it off). Ranking is the section score (watched journal and researcher first, then topic breadth, abstract present, open full text, core journal), ties to the most recently stored. Held-back papers are not marked as sent, so they are considered again next week for up to `max_catchup_days` (28) and then age out; the footer states how many were held back and the issue's stats and the build output say why. Tested: the test edition of 178 items becomes 44 (30 direct, 2 mechanisms, 9 preprints, 3 trials, 134 held back) and 45 KB. The summary step now runs after ranking and capping, so only papers that will be printed cost model calls (the earlier 15-minute build summarised all 178; a capped edition summarises at most about 65, so roughly 5 minutes on this key).

**Bug found by the cap work:** a rebuild of an unpublished edition found no papers to summarise, because the old edition's own items still counted as already sent while the summary pre-pass ran. The edition silently fell back to abstract extracts. Fixed; the earlier test passed only because it checked that the model was not called again, which was trivially true. It now checks that the rebuild finds and reuses the cached summaries.

**Carry-over rule (owner, 2026-10-31: "they should drop out after 7 days ... stay only for the next edition and that too penalized a bit for being late ... only the old results that genuinely have strong signals like full text will come up").** `newsletter.max_catchup_days` is 14 (was 28): an edition looks back this week plus one more. A paper held back by a cap (or left out by a newsletter switch) is therefore a candidate in the next edition and in no later one; a single skipped week is still covered. A paper first stored before the normal 7-day window is a carried-over paper and loses `newsletter.carryover_penalty` points (default 3, the weight of held full text or a followed source) from its ranking score; the rule that followed journals and researchers are chosen first still applies, and within that group carried-over papers rank below new ones. Retraction alerts are never penalised. Editions record how many carried-over papers they hold (`stats.carried_over`, a note, and a flag in each item's snapshot). Validation: `max_catchup_days` must be at least `lookback_days`, the penalty 0 or more. Consequence to know: a permanently over-subscribed section drops papers after two chances.

**Layout and wording (owner, 2026-10-31: the HTML "didn't look good in drafts"; greeting and outro "similarly, not the same" as the older agent).** The cause was a `<style>` block with CSS classes, which Gmail's compose window (opening a draft) removes. The template is now tables with inline styles only (modelled on the older agent's template): slim header band, greeting and introduction, a one-strip at-a-glance (section counts, how many have full text held, how many are from followed sources), section headers (red for retractions, amber for preprints, teal for trials), one row per paper with pills coloured by meaning (`rendering.badge_tone`) and thin divider lines, links, closing, signature, footer. A first version used a 640 px column of boxed cards; the owner found it looked like a phone layout on desktop, so it was made denser and wider (760 px, smaller type, no boxes). Wording is profile settings in the style of the older agent's (Curt: "Hello Curt," / a round-up introduction / "This week's papers:" / "That's everything for this week. Wishing you a restful weekend." / a signature with name, title and a disclaimer; email and phone are optional and left out of git) and `accent_color` is validated. Tests: no style block or CSS, balanced tables, every element carries its own style, wording order, escaping, blank fields omitted. Size: about 1.6 KB a paper (30 papers = 48 KB); the 95 KB safety net still trims a very full edition. Not verified in Gmail itself (checked in a browser at desktop and phone width); open the first real draft and tell me what to adjust.

**Readability fixes (owner, 2026-10-31).** (1) Category (subject) headings are now a bold 15 px heading in a tinted band with a coloured bar, in place of small grey capitals; (2) more room between results (22 px above and below each), around the introduction (32 px above) and the outro (40 px above the closing line, 36 px below the signature). (3) Abstract spacing: a survey of the 186 stored abstracts found 47 with a glued section label or sentence end (most from OpenAlex, whose rebuilt abstracts arrive with line breaks stripped). `domain/text_repair.py` repairs those two kinds of join precisely (24 abstracts, 107 spaces on the real data; only spaces are ever added; idempotent) and runs in `PaperRecord.__post_init__`, so it covers every source and happens before the field gate sees the text, and for abstracts recovered from PDFs; `fix-abstracts [--apply]` repairs stored ones. A side effect: with the labels separated, the extractive fallback summary now finds the concluding section instead of quoting the garbled opening. Lowercase joins (`selfadministeredelectronic`, `andhormonal`, about 1% of abstracts) are split with the `wordsegment` dictionary (owner approved installing it, 2026-10-31; Apache 2.0, a project dependency). Measured on the real abstracts and titles: a loose rule (unknown word, common parts) proposed 139 splits of which about 130 were wrong (`atogepant`, `lasmiditan`, `semaglutide`, `neuroinflammation`, `phonophobia`, `sphenopalatine`, Indonesian and Slovenian words), and word frequencies cannot separate joins from real words (both have parts occurring millions of times). So a split needs all of: 10+ letters and not a known word; every part 4+ letters (or a joining word: and, the, for, with, from, that, this, such, were, was, are, not) and a common dictionary word (1M+ occurrences); no part a prefix or suffix (a list of about 60: neuro, anti, post, -able, -ing, -ity ...); mostly-English text (75% of 4+ letter words known) so non-English abstracts are untouched; not next to a dot, slash, hyphen, at-sign or colon; and not on a keep-whole list of real compounds (blockchain, chatbot(s), nailfold, braincomms, clinicaltrials). Result on the real data: 7 splits, all correct, none wrong (`andhormonal`, `selfadministeredelectronic`, `highincome`, `outcomespecific`, `scalptargeted`, `scalpbased`, `terminologysuch`). Known limit: a future real compound of two common whole words that is not on the keep-whole list would be split into two words, which stays readable; add it to the list. The `fix-abstracts` dry run now reports 4 abstracts (8 spaces) left to repair after the structural fixes were applied.

**Header and headings as in the older newsletter (owner, 2026-10-31, with the old Gmail message as reference).** The older message's markup was read directly: header band with a 12 px capitals label ("Research briefing"), a 27 px title ("Weekly Migraine Research Digest"), a 14 px subtitle ("Curated insights from recent publications"), then a 13 px row with the date and "N results"; category headings were `<h2>` at 19 px / 27 px, colour #172033, with a 2 px accent rule; accent #155eef. Ours now uses the same (`newsletter.header_label`, `title`, `subtitle`, `accent_color`), drops every "week ending" (header, text version and subject) and builds the subject from `newsletter.subject` (placeholders `{title}` and `{date}` only, validated). Because our categories sit below a kind-of-research level (Direct research, Preprints ...), that level is a slim coloured strip with small white capitals, so the two levels do not compete.

**Abstract recovery for editorials (owner, 2026-10-31: "didn't I put forward a fix on cases where we had no abstract by analyzing their full text?").** Yes, and it works (abstract heading, summary heading, structured block, Keywords-line block, Introduction), but a check of the production data found 1 of 63 held full texts with no abstract, an editorial whose PDF has none of those headings: after a block of front matter the prose simply starts. A final rule was added: take the first real paragraph of the body (`domain/fulltext._opening_of_the_body`; kind `pdf_opening`, labelled "No abstract: summary from the opening of the text"): 40+ words, at least two sentences, not mostly capitals, starts like a sentence, no front-matter wording (uncorrected proof, copyright, licence, e-mail, conflicts of interest, dates ...), stopping at the first heading-like line or 1,400 characters. On the real editorial it recovers the right 146 words. Run over the 63 stored texts it gives sensible introduction paragraphs; it only ever applies to a paper with no abstract. Still without an abstract: 31 other papers (of 218) where the source gave none and no full text is held; there is nothing to recover from.

**No-abstract papers go last (owner, 2026-10-31: "Move them to the end of their sections with no abstract available").** In each section papers with no abstract of any kind (a recovered one counts as having one) are ordered after the others; under Direct research, where papers are grouped by category, they are gathered into one last group headed `newsletter.no_abstract_heading` ("Without an abstract") so they are not scattered through the categories; sections without categories just list them last; retraction alerts are exempt. Selection is unchanged (the caps still rank by score, in which an abstract is worth 1 point). The stored-text recovery was widened in the same pass: it now also covers texts built from XML (a JATS article with no abstract element, such as a Springer "Key Summary Points" paper), recognises the "## Introduction" heading of such texts, and removes the `[]` debris of stripped citations; the badge now reads "Abstract recovered from the full text".

**Open:** the right caps for the client (question 2); papers held back twice age out unseen (by design since 2026-10-31, see below), so a permanently over-subscribed section loses items without a trace beyond the held-back count; the title of the digest is set in the profile (`Curt migraine research digest`) and can be changed.

**Review of the rendered edition (2026-10-31).** Three fixes after reading the real HTML: trials now say "Registered" (a registry entry is registered, not published); the footer reads "from X to Y"; and papers with the field's word in the *title* rank above papers that only mention it in the abstract (+2 in the section score), so with caps in force the cut falls on the weaker matches first. The gate is a word-presence test by design (the owner's rule), so a trial comparing people with epilepsy against a migraine group passes it; ranking is the soft way to treat "mentions" differently from "is about". A stricter relevance rule (for example, at least two mentions or the word in the title for non-watched sources) is a client decision, not made.

**Freshness rule (owner, 2026-10-31: "drop the idea of papers that were published earlier but appearing now in the platforms to appear in this week").** `newsletter.freshness.max_publication_age_days` (default 7, tunable, null = off). Age is the days from publication to the day we first stored the paper, not to the build date, so a paper held back by a cap stays eligible the next week. Unknown publication dates are never left out; a month-only date is treated as its last day; retraction alerts are exempt (a retraction of an old paper is still news). Effect on the stored test data: of 178 matching papers, 79 are left out as stale, 64 more are held back by the caps for next time, and 35 are in the edition (30 direct, 2 preprints, 3 trials; the 2 related-mechanism papers were both old); 33 KB; ages at discovery run from -1 (a future-dated print issue) to 7 days, median 3. Much of that 79 is the one-off effect of seeding a fresh database (a first run stores everything the sources and OpenAlex's 14-day lookback returned at once); in daily operation it will be late-indexed papers only, and the count is reported each week. Consequence to keep in view: the Related mechanisms section, which draws on slower-indexed journals, may often be empty or very short under a 7-day limit.

**Printable full text and ranking (owner, 2026-10-31: "option 2, printable in any way, I don't care about the license. Keep that in mind about this agent").** Print policy default is now "print regardless of licence" (`fulltext.print_text_regardless_of_licence: true`); `--reapply-policy` recomputed the stored texts (17 changed on the test copy). Ranking weights became settings (`newsletter.ranking.weights`), and the full-text signal is "printable full text in hand" at weight 3, level with a watched journal, replacing the old +1 for open text. Effect on the test data: of 94 fresh direct papers, 21 have printable text; the 30 chosen now include 15 of them (was 8), and the number of watched-journal papers among the 30 falls from 20 to 16 (5 of the 21 watched-journal papers have no text and now rank below papers that do). That trade-off is the weight's job: lower it to favour watched sources, raise it to favour papers the printed edition can carry in full. Full text counts only if fetched before the build, so the weekly job must run `fulltext` before `newsletter build`. Option 3 (a smaller bonus when a PMC ID or free PDF link exists but the text is not fetched yet) was offered and not chosen.

**Full-text label and abstract recovery (owner, 2026-10-31).** The digital edition now labels papers whose full text we hold and may print ("Full text available"); 15 of the 35 items in the test edition carry it. For the rare paper with no abstract anywhere else, the abstract is recovered from the first pages of its PDF or text (heading, summary, structured block, block before the Keywords line, last resort the Introduction), stored with its source (`papers.abstract_source`), shown with a provenance label, and the paper is re-judged for relevance. Of 33 gate-passed papers without an abstract in the test data, 15 were book chapters, 3 deposits or datasets, and 15 journal articles; only about 5 had any text or PDF route, and none was recoverable (an editorial and a correction notice have no abstract to find; one PDF was blocked with a 403; one was a book index). So the case is as rare as expected.

**Blind test of the extractor** on 29 stored PDF texts of papers whose true abstract is known: an abstract found in 27, accurate (at least 80% of the recovered words in the true abstract and at least 60% of the abstract captured) in 25. Heading-based extraction: median precision 0.99, recall 1.00 (18 papers); structured abstracts 0.95 / 0.98 (9); the Introduction fallback is not an abstract (precision 0.76, recall 0.11) and fires for 1 of 29. Not found: a two-column layout where a "Received on" sidebar interrupts the abstract (returns nothing rather than a guess) and a correction notice. Three bugs from this test were fixed: a stop rule that ended an abstract at a sentence beginning "Published examples include...", Frontiers-style PDFs with no "Abstract" heading (abstract sits before "Keywords"), and the case-sensitive run-in "Abstract Text...".

**Wrong-paper link found and guarded.** For one paper (a 2026 gepants review) OpenAlex supplied a "best free PDF" that is a different, 2023 article; the full-text stage stored that article's text under the 2026 paper, which a printed edition would have carried. Crossref had the correct link. Now a PDF or plain text is accepted only if the paper's DOI or most of its title words appear in its first pages (JATS from DOI-keyed APIs is exempt), the next link is tried when it fails, and `fulltext --verify-stored` removed the stored wrong text. One in 68 stored texts was wrong (about 1.5%); the check is cheap and applies to every new fetch. OpenAlex's PDF links should be treated as hints, not facts.

**Edition review (owner, 2026-10-31: "look at the edition first").** Reading the rendered edition found: (1) no markup, link, duplicate or encoding defects; (2) a side effect of weighting printable full text at 3: of 94 fresh direct candidates 68% were open access but 28 of the 30 chosen were, because paywalled papers rarely have text to fetch, and four *Neurology* papers (a watched core journal) lost a tie at the cutoff. Simulated alternatives on the test data (full text / open access / watched sources in the 30): weight 3: 15 / 28 / 16 of 21; weight 2: 12 / 27 / 17; weight 1: 9 / 24 / 20; weight 3 with watched sources first: 11 / 25 / 21; weight 0: 5 / 22 / 20. The owner chose **watched sources first** (`priority_to_watched`), which keeps the weight at 3. (3) Letters, replies and corrections: of 168 relevant stored journal articles about 9 were not research (four PubMed-typed letters, reader responses and an editors' note from *Neurology*, a "Reply to:", and corrections or errata, one of which was the watched-journal paper that "had no abstract"). They had only been kept out by low scores, and watched-first exposed them. New content types `letter` and `erratum` (from publication type or an unambiguous title prefix: Correction:, Erratum, Reader Response, Reply to, Comment on, Editors' Note; a 'Letter' with a real abstract stays as research) are excluded from digests and from the full-text queue by default, stay in the database and the app, and are in `digest.exclude_content_types`. 8 stored papers changed type. Titles no longer show the full stop that some indexes append. Resulting edition: 35 items (30 direct, 2 preprints, 3 trials), 36 KB, all 15 real watched-source papers in, 14 with full text, no letters or corrections, every item has a summary. Editorials are still included (no decision made); a stricter relevance rule is also still a client decision.

## 16. Application side: API (started 2026-10-31)

**Scope (owner, 2026-10-31).** The application is for Curt only. It has two jobs: (1) endpoints that change the newsletter's configuration: switch the search topics (categories), the journals, the researchers and the sources on or off, which changes the newsletter runs only; (2) an endpoint that searches the migraine papers in the database, filtered by date, journal, author and researcher and sorted by date, relevance or full-text availability. Built: an HTTP API (FastAPI) with both, plus read routes for editions and health; no front end yet. The form of the front end (web page, installable web app, phone app, or the API alone) is still question 12 and has not been decided.

**Design.** `application/queries.py` is a read-only query layer (papers, one paper, full text, retractions, editions, stats, runs, profile); `interfaces/api/app.py` is a thin web layer over it; the CLI starts it (`research-agent api`, `research-agent api token`). Routes and parameters are in the README. Everything the API shows is filtered by the profile (`API_PROFILE`, one in v1) and by the same hidden content types as the digest.

**Safety built in and tested.** Bearer-token access on every data route, compared in constant time, several tokens allowed; the app refuses to be created without a token; the database connection is opened read-only (SQLite `query_only`, Postgres `default_transaction_read_only`) so even a code bug cannot write; every non-GET method returns 405; no-store caching and safe headers; docs off by default; CORS only for configured origins; whitelisted run statistics only (nothing else from a run's stored record is exposed); no credential appears in any response (tested). A bug found on the way: the web framework misread the token dependency when the module used postponed annotations, making every route ask for a `creds` query parameter; fixed and covered by the access tests.

**Supporting change.** `profile_matches.watched` (stored when a match is made, backfilled by migration 0009: 37 of 233 test matches) so the API can filter "from a watched source" in SQL and the edition ranking uses the same fact.

**Checked live** on the scratch database with the real server: no token gives 401; stats, search (`q=cgrp`, 27 papers in 14 days), watched papers with full text (15), one paper's detail, its full text as plain text, hidden items (170 shown by default, 230 with `include_hidden`), the edition HTML (36 KB) and a POST (405) all behave. 35 API tests cover access control, visibility, filters, paging, validation, detail, full text, retractions, editions, stats and the no-write guarantees.

**Not built (open):** the front end; rate limiting; per-user accounts and per-user history such as "mark as read" (v2.0, and the API is deliberately read-only); full-text search beyond word matching on title and abstract (a Postgres full-text index would be the upgrade); a filter by subject (stored as JSON, needs a join table or an index for a portable query); hosting and HTTPS (deployment phase); a read-only database role on Supabase.

**Search (2026-10-31).** One route, `GET /v1/papers`, built on the existing query layer. Filters: publication-date range, journal (a followed journal's key is matched by ISSN or exact name; any other text by part of the name), author (every word of the name in the author list, accents and case ignored), researcher (the identity-checked link, not name text), plus the earlier filters. Different filters combine with AND, repeated values of one filter with OR. Sorts: newest stored, publication date, relevance, full text held first; ascending or descending. Relevance is computed in SQL so paging and totals stay correct: 3 points per query word in the title, 1 per word in the abstract, plus the profile's own ranking weights for a followed source and for held full text. `GET /v1/papers/filters` lists what a form needs (journals in the database with counts, followed journals and researchers with counts, date ranges). Two derived columns make the author and researcher filters portable SQL instead of JSON text matching: `papers.author_names` (folded "family given initials" per author) and `profile_matches.entity_tags` (`|journal:key|researcher:key|`, ambiguous researchers excluded); both are set automatically whenever authors or entities are set, and migration 0010 backfilled them (228 of 233 test papers have author text, 37 matches have tags). Papers with no exact publication date are left out of date-range searches and sort last by publication date (2 of 233 in the test data).

**Newsletter settings (2026-10-31).** Table `newsletter_selections` (profile, kind, key, enabled, updated_at); no row means on. Kinds: topics (the 12 subjects), journals (10), researchers (30), sources (PubMed, Europe PMC, Crossref, OpenAlex, ClinicalTrials.gov). `NewsletterService._gather` reads it and keeps a paper while at least one of its routes in (a matched topic, a followed journal, a followed researcher with a confirmed identity) is on, and, when any source is off, while at least one source that found it is on; a paper with no route in is untouched; retraction alerts are never filtered. A journal or researcher switched off also stops counting as "watched" (no label, priority or score bonus). Papers left out are not marked as sent, so they come back within the 28-day catch-up if switched on again. The settings cannot leave the newsletter with no source, or with no topic, journal or researcher. Each edition stores what was off and how many papers it cost, and the footer lists only the sources that were on. Routes: `GET /v1/newsletter/config` (any token), `PATCH /v1/newsletter/config`, `PUT /v1/newsletter/config/{kind}/{key}`, `DELETE /v1/newsletter/config` (admin token). Requests are all-or-nothing and validated (unknown keys, non-boolean values and extra fields are 422).

**Web page (2026-10-31, owner chose a simple web page).** `interfaces/web/index.html` and `app.js`, served by the API at `/app` (and `/` redirects there). Search tab: every filter and sort of `GET /v1/papers`, results as cards (followed-source, full-text, preprint and retracted badges, summary, relevance when sorted by it, the full text readable in place), paging; Settings tab: tick boxes for the 12 topics, 10 journals, 30 researchers and 5 sources, saved as one PATCH with only what changed, and a reset button. The token lives in the tab's session storage. Served with a content security policy (own script only, no inline script, calls only this server, cannot be framed); the script builds elements from text nodes only and follows a link only if it is http or https (a test fails if unsafe DOM calls appear). Checked in a real browser in dark mode and at phone width: search, saving two switches (visible in the database afterwards), no console errors.

**Rate limiting and roles (2026-10-31).** `interfaces/api/ratelimit.py` is a per-address sliding-window limiter in process memory (120 requests a minute by default, health checks exempt, 429 with Retry-After). Wrong tokens are counted per address; after 10 a minute, further wrong or missing tokens get 429, but a valid token is never refused (so a stranger cannot lock real users out, which matters because behind a proxy everyone can look like one address). Who is asking is the connecting address, or the last entry of the one header named in `API_CLIENT_IP_HEADER`; `GET /v1/whoami` shows what the server sees. `infrastructure/db/roles.py` and `research-agent db roles [--pooler-host H] [--pooler-port P]` create `curt_api_read` and `curt_api_config` on Postgres with explicit RLS policies (the tables have RLS on and no policies, so an unprivileged role sees nothing until given one), verify that neither role has elevated rights, and write the two connection strings to `.env` without printing them. The jobs keep using the owner connection; only the API uses the limited roles. Applied to the Supabase project and checked live (2026-10-31): the first attempt failed because Supabase's postgres user cannot state SUPERUSER-related attributes, so the command relies on the safe defaults and verifies afterwards; failures show only the database's message (the first error had echoed a throwaway password). The read role is refused every write and every table it was not given; the config role is refused everything except its one table.

**Hosting and scheduling (2026-10-31).** Owner choice: Railway for everything (first proposed: GitHub Actions for the schedule and Vercel for the API; changed after comparing, because one always-on server removes the serverless workarounds). One `Dockerfile` (python 3.12, poppler for PDF text, editable install so config/ and migrations/ sit next to the code, non-root user) serves three Railway services: `api` (default command, public domain, only the two limited database roles, one replica), `daily-collect` (start command `research-agent job daily`, cron `30 5 * * *`) and `weekly-newsletter` (`research-agent job weekly`, cron `0 6 * * 5`), all UTC. `src/research_agent/jobs.py` holds the job logic so each service needs only a short start command: daily = `db upgrade`, `seed`, `collect --overlap JOB_OVERLAP_DAYS`; weekly = freshness guard (`status --max-age-hours 36`), `db upgrade`, `fulltext --limit JOB_FULLTEXT_LIMIT`, `newsletter build`, then a Gmail draft only when Gmail credentials exist (never sends). A job stops at the first failing step and exits non-zero, and has a hard time limit (45 and 150 minutes) because Railway never stops a hung cron run. The edition is stored in the database and read in the web page's new Editions tab (the file output is no longer used on the host). Railway's config-as-code files are deprecated for new services, so service settings are entered in the dashboard (`DEPLOYMENT.md`). Database access is through Supabase's session pooler (the direct host is IPv6 only); `session.engine_options` also handles the transaction pooler. The tests-on-push workflow `ci.yml` stays on GitHub. Not verified: the Docker build, a real Railway deployment and cron run, Railway's `X-Real-IP`, and the pooler from Railway.

**Database round trips (2026-10-31).** The first real run on Railway showed that a collection is bound by the distance to the database: it makes thousands of small statements, and over a 200 ms link they took longer than the job limit, all inside one transaction. Measured on a live collection (94 searches, 334 records, 218 new papers): 8,564 statements in 2 commits. Fixed without changing results: hits, matches, retraction checks and papers are loaded in a few queries instead of one per paper (`Repository.matches_for`, `hit_meta_many`, `papers_by_id`, `paper_ids_with_checks`, `apply_match`), the enrichment steps share one set of lookups, a paper just looked up is not looked up again when merged, and a new paper is not checked for existing links, hits or taken identifiers. Now 4,493 statements, and the run commits after every search (98 commits), so an interrupted job keeps its work and no transaction stays open for minutes. A test bounds the cost of one more paper (17 statements, limit 22). The Railway services should also run in a region near Supabase (Stockholm): `DEPLOYMENT.md` section 3.

**Safety of the one write path.** The data routes still use the read-only connection. Switches are written over a second connection that exists only when `API_ADMIN_TOKENS` is set, are accepted only with an admin token (a read token gets 403, no token 401), and touch only `newsletter_selections` (tested: papers and matches are unchanged by any switch). On Supabase this connection (`API_ADMIN_DATABASE_URL`) should use a role that can only write that table.

**Tests.** 41 new tests (361 offline in all, 95% coverage): name folding and tag helpers, the selection rules, the settings service (validation, atomic changes, reset), edition behaviour (topic, journal, researcher and source switches, roll-over after switching back on, retraction alerts, footer, stored data untouched) and the HTTP layer (each filter and sort, combinations, filters route, token roles, bad requests). Checked live on a migrated copy of the scratch database.

**Decisions made on the owner's behalf, to confirm:** (1) a switched-off journal or researcher stops being a *route in* but a paper from that journal still appears if one of its topics is on (the alternative is to drop everything from a switched-off journal); (2) "sources" means the five discovery sources, not the newsletter sections; (3) the search's relevance is a simple points score, not the newsletter's full ranking (which also counts topic breadth and core journals and cannot be computed in SQL).

**Weekly timing and edition date (owner, 2026-10-31).** The weekly job moves to Thursday 20:00 IST (cron `30 14 * * 4`, UTC), 9 hours after the 05:30 UTC daily collection. The edition is dated Friday through `newsletter.edition_weekday` (4): the next Friday on or after the run's UTC day, while the content cutoff stays the moment the job starts (`cutoff_for` already treats a future edition date as 'up to now'). The weekly freshness guard is 12 hours (was 36) so a failed Thursday collection fails the weekly job rather than silently dropping that day's papers. Papers collected on Friday go into the following Thursday's edition (selection is by first-stored time, never resent).

**Web app link and sign-off (owner, 2026-10-31).** A box under the At a glance panel links to the web application (`newsletter.app_link`, HTTPS only; optional title, text, button and note). It is deliberately not repeated in the outro. The outro's team name (`newsletter.signature.name`, now "Healthcar AI Research Team") is set in 20 px bold navy beside a 4 px accent bar so the sign-off is easy to see.

**Search upgrades parked for v2.0 (owner, 2026-10-31).** Today the search is keyword matching on title and abstract only. Planned later, in this order: (1) keyword search inside the stored full text using the database's text search, with snippets and a checkbox, no new column on existing tables; (2) meaning-based search from title-and-abstract vectors held in their own table, merged with the keyword ranking; (3) full-text passage vectors only if needed. Details and trade-offs are in `Progress.md` section 8.
