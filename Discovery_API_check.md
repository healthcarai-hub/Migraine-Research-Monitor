# Discovery API Check

Source: `Akeso Migraine Monitoring System Sources - Adnan.xlsx`, sheet **Agent - Discovery APIs** (10 services), plus the **Agent - Search Subjects** dictionary (12 rows) used as probe queries.
Validated: **2026-10-30**, read-only GET requests from a developer laptop IP, no accounts, no API keys, nothing purchased.

**Evidence tags:** `[LIVE]` observed in a request in this run (status codes, headers, counts); `[DOCS]` stated in provider docs or the response text, not independently tested; `[UNVERIFIED]` from general knowledge or not checked, confirm before relying on it.

## 1. Summary

| # | Service | Sheet role | Reachable by script? | Key needed? | Price | Verdict |
|---|---------|-----------|----------------------|-------------|-------|---------|
| 1 | Google Scholar | Existing / A+B | HTTP 200 for 2 requests, but **no API**; scraping is against Google's terms and gets CAPTCHA'd `[UNVERIFIED]` | n/a | no official offer | Keep only the existing **email-alert** route. Do not build scraping on it |
| 2 | PubMed / NCBI E-utilities | Primary | Yes, 200, ~1.0-1.4 s | optional | free | **Ready.** Core source |
| 3 | Europe PMC REST | Primary | Yes, 200, ~1.3 s | no | free | **Ready** |
| 4 | Crossref REST | Support | Yes, 200, ~1.4-1.6 s | no | free | **Ready** (use `mailto`) |
| 5 | OpenAlex | Support | Yes, 200, 1-2.5 s | **Recommended; needs a decision** | free tier, small daily budget | **Works, but metered**; see below |
| 6 | bioRxiv | Secondary / B | Yes, 200, ~5 s | no | free | **Works**, no topic filter; find items via Europe PMC instead (2.6b) |
| 7 | medRxiv | Secondary / A | Yes, 200, ~2.4 s | no | free | **Works**, same API; find items via Europe PMC (2.6b) |
| 8 | Publisher TOC / RSS | Secondary | The sheet's URL returns **403**; 8 other publisher feeds work | no | free | **Partly.** Needs per-publisher feed list |
| 9 | Cochrane Library | Secondary / A | **Blocked: 403** (Cloudflare challenge) on home, search and RSS | n/a | n/a | **Not usable directly.** Use PubMed/Crossref as proxy |
| 10 | ClinicalTrials.gov v2 | Optional / A | Yes, 200, ~0.5 s | no | free | **Ready** |

## 2. Per-service detail

### 2.1 Google Scholar
* `scholar.google.com` and a sample search both returned 200 `[LIVE]`. I limited myself to those two requests.
* There is no official API `[UNVERIFIED]`. Automated querying is against Google's terms and commonly triggers CAPTCHAs/blocks `[UNVERIFIED]`; paid third-party SERP APIs exist but I did not evaluate their pricing or terms.
* The existing agent uses Scholar **alert emails** read from Gmail, not the website. Keep that. The sheet already says "continue current agent".
* Alert emails are limited by what the client has set up in Scholar (alert count, relevance); the agent cannot discover beyond them.

### 2.2 PubMed / NCBI E-utilities
* `esearch` works. `migraine[tiab]`: **49,080** records total, **237** entered in the last 30 days `[LIVE]`.
* **Limit: 3 requests/second without a key** (header `X-Ratelimit-Limit: 3`) `[LIVE]`. A free key raises this to 10/s: **confirmed 2026-10-30, response header `X-Ratelimit-Limit: 10` with the project key** `[LIVE]`. The 10/s ceiling itself was not stress-tested (my 20-request test ran sequentially at ~1 request/s, so it did not exceed the old 3/s limit).
* **Price: free.**
* Supports date types: entry date (`edat`), publication date (`dp`), reldate windows, which suits the "overlapping rolling windows, indexed vs publication date" requirement.
* The sheet's author-link syntax does not work (see Researchers_source_validation.md).
* Pagination ceilings (`esearch` result window) `[UNVERIFIED]`: check before paging deep.

### 2.3 Europe PMC
* `search` works; query `migraine` returned **125,448** hits; the response includes `nextCursorMark` (cursor pagination) `[LIVE]`. A date-bounded query for Oct 2026 returned 417 hits (it searches more than title/abstract, so it is not comparable to the PubMed figure) `[LIVE]`.
* No rate-limit headers returned; **no numeric limit found** `[UNVERIFIED]`. No key. Free.
* Adds abstracts/full text for open-access items. Full-text licensing varies per record `[DOCS]`.

### 2.4 Crossref
* Works. Observed headers: `x-rate-limit-limit: 1` per `1s` on `/works` list queries, and `5` per `1s` on `/journals/{issn}` (public pool) `[LIVE]`. A "polite pool" with higher limits for requests carrying a contact email is described in docs `[DOCS]`; I sent a `mailto` on later calls but did not capture its headers.
* Free, no key. Good for DOI metadata, ISSN-to-journal identity (all 10 journals resolved) and version/retraction checks.
* Caution: filtering by `from-index-date` returns metadata updates, not new papers (e.g. Neurology: ~11k records since Oct 1). Use created/published dates for freshness.

### 2.5 OpenAlex
* **Update 2026-10-30:** a free API key was created for the project mailbox, activated, and tested: HTTP 200, allowance `x-ratelimit-limit: 10000` credits (`$1`/day), about 10 credits ($0.001) per simple request, so roughly 1,000 requests per day `[LIVE]`. The findings below describe the keyless state seen earlier.
* Works, but metered. Responses carried: *"No API key, so this request shares one free daily budget ($0.10) with everyone on your network's IP address. Get a free key with its own $1/day"* `[LIVE]`. Each simple request cost **$0.001 (10 credits of a 1,000-credit daily allowance)**; I used about 35 requests today.
* Pricing beyond the free budget (prepaid balance headers are present) is not verified.
* The notice offers key creation by posting the user's email address. **I did not do this**: it registers an email with a third party. Decision for you/Curt (see Progress.md).
* Practical capacity without a key: ~100 simple requests/day per IP, shared. Enough for periodic author-disambiguation runs, not for daily citation-network expansion at scale. With a key, ~1,000 per day `[DOCS, from notice]`.
* Behaviour has changed versus older public descriptions of OpenAlex (previously open and rate-limited by request count). Re-check at build time.

### 2.6 bioRxiv and 2.7 medRxiv
* `https://api.biorxiv.org/details/{server}/{from}/{to}/{cursor}` returns 200 for both servers `[LIVE]`.
* Page size **30** with cursor paging. bioRxiv Oct 1-30: **6,742** records (5,064 new papers); medRxiv Oct 1-7: 478 records (397 new) `[LIVE]`.
* The response echoes `category: all`. I did **not** find or test a keyword filter, so migraine filtering has to happen client-side after pulling everything in the date window (~5k bioRxiv records/month, roughly 170-225 pages/month, at ~5 s per call for a week-range request) `[LIVE]` for the numbers; no-keyword-filter is `[UNVERIFIED]`.
* No key, free, no limit published that I found `[UNVERIFIED]`. All items must be labelled non-peer-reviewed (sheet requirement).
* **Alternative tested 2026-10-30: Europe PMC preprint search works and is the better route (see 2.6b below).**

### 2.6b Europe PMC as the preprint finder (tested 2026-10-30)
Query form: `TITLE_ABS:"migraine" AND SRC:PPR AND FIRST_PDATE:[from TO to]` against the same `/search` endpoint as 2.3. One request, about 1-2 s. `[LIVE]`

| Check | Result |
|-------|--------|
| Migraine preprints, all time | 1,145 |
| Migraine preprints, Oct 1-30 2026 | 18 |
| Migraine preprints, Oct 1-7 2026 | 6 |
| All preprints Oct 1-7 2026 | 4,613 (so the keyword filter removes >99.8%) |
| Records with an abstract (28 sampled) | 28 of 28 |
| Servers seen (28 migraine preprints, Sep-Oct) | Research Square 9, medRxiv 9, Preprints.org 7, bioRxiv 2, PsyArXiv 1 |

**Recall check against the sheet's own servers.** I pulled all of bioRxiv (1,460 records; 1,430 unique DOIs because of versions) and medRxiv (478 records) for Oct 1-7 and filtered locally for `migrain*` in title/abstract: **1 hit in total** (a bioRxiv mouse pain paper, 0 on medRxiv). Europe PMC returned that same paper, so on this sample it missed nothing. The sample is tiny (one overlapping item), so this shows the route works, not that its recall is proven. The strict phrase and the wildcard `migrain*` returned the same 6 records.

**Cost comparison:** the full local pull took 65+ requests and several minutes (one run hung and had to be restarted; the bioRxiv API is slow, about 5 s per page of 30). Europe PMC answered the same question in one request.

**Things to know**
* Europe PMC also returns preprints from **servers that are not in the sheet** (Research Square, Preprints.org, PsyArXiv). 5 of the 6 migraine preprints in the test week came from Research Square or Preprints.org, not bioRxiv/medRxiv. The client should decide whether to include them; they must carry the same "non-peer-reviewed" label.
* The records have no field showing publication status or a link to the published version (none seen in the 28 sampled), so preprint-to-article matching still needs DOI/title matching or the bioRxiv/medRxiv `published` field (which was `NA` in the new records I sampled; its behaviour for later-published preprints is untested).
* Keep the bioRxiv/medRxiv `details` API for looking up a specific DOI, not for bulk discovery.
* **Decision 2026-10-30:** the extra servers (Research Square, Preprints.org, PsyArXiv and any other `SRC:PPR` server) are **included**, all labelled non-peer-reviewed.

### 2.8 Publisher journal TOC / RSS
* The URL in the sheet for this row is the Cephalalgia homepage (`journals.sagepub.com/home/cep`): it returns **403** to scripts `[LIVE]`. It is a webpage, not a feed.
* Eight publisher feeds I located work (Cephalalgia, Headache, Springer-hosted JHP, Neurology, Lancet Neurology, Frontiers, BBI, Redox Biology); JAMA Neurology and Nutrients are unresolved. Full table in Journal_source_validation.md.
* Free, no key. Limits unpublished `[UNVERIFIED]`.

### 2.9 Cochrane Library
* `cochranelibrary.com`, its review search and `/rss` all returned **403 with a Cloudflare "Just a moment..." challenge** `[LIVE]`. A script cannot get through without circumventing the protection, which I did not attempt.
* I did not find a public API for Cochrane Library `[UNVERIFIED]`.
* **Proxies that work:** PubMed lists *Cochrane Database Syst Rev* items: 113 mention migraine in title/abstract, 4 in 2025-26 `[LIVE]`. Crossref resolves the journal ("Cochrane Database of Systematic Reviews") `[LIVE]`. Europe PMC returned 520 hits for a loose query, which includes false positives and should not be quoted `[LIVE]`.
* Migraine Cochrane reviews are rare (about 2 per year in the PubMed window), so a PubMed-based feed is likely sufficient.

### 2.10 ClinicalTrials.gov (API v2)
* `GET /api/v2/studies?query.cond=migraine&countTotal=true` returned **1,827** studies in ~0.5 s; no key; free `[LIVE]`.
* A rate limit exists in the docs (I recall roughly 50 requests/minute per IP) `[UNVERIFIED]`.
* Supports update-date filtering for "new and updated" records `[UNVERIFIED]`; test before relying on it. Registry records are not peer-reviewed publications and should be labelled separately.

### 2.11 Retraction detection (Instructions rule 8; tested 2026-10-30)

**Question:** which free source reliably flags a retracted migraine paper? **Method** `[LIVE]`: took all 18 migraine papers PubMed flags as `Retracted Publication` (`migraine[tiab] AND "Retracted Publication"[pt]`, all have DOIs) and looked each up in Crossref (`/works/{doi}`, field `updated-by`), OpenAlex (`is_retracted`) and Europe PMC (`pubType`). Then the reverse: took OpenAlex's retracted migraine set (50 works, 40 with a PMID) and looked at the 22 that PubMed does not flag. Three recent non-retracted papers were used as controls and were unflagged in all four sources.

| Source | Flags (of the 18 PubMed-flagged) | Not flagged | Notes |
|---|---|---|---|
| PubMed `Retracted Publication` type | 18 (by selection) | - | Comes free inside the `efetch` XML we already pull. |
| Crossref `updated-by` (retraction/withdrawal) | 10 | 8 | Retraction Watch data appears for 9 flagged records, publisher-deposited for 6 (some have both). |
| OpenAlex `is_retracted` | 12 | 6 | Cheap boolean on every work record. |
| Europe PMC `Retracted Publication` type | 12 | 5 (+1 not indexed) | Same flag family as PubMed but lags it. |

* **No single source is complete.** Crossref, OpenAlex and Europe PMC together still miss **6 of the 18** that PubMed flags. Their retraction notices are dated 2022-2026 (three are 2022, 2023, 2024), so this is a persistent gap, not just lag.
* **PubMed also misses real ones.** Of the 22 OpenAlex-flagged papers PubMed does not flag, **11 are retraction/withdrawal notices themselves** (titles start "Retraction..." or "WITHDRAWN:") and **11 are original papers with a Crossref retraction/withdrawal record** that PubMed does not mark (several look like old Cochrane reviews, e.g. "Propranolol for migraine prophylaxis", 2004; I did not check the journal field). That matters because PubMed is our Cochrane stand-in.
* **Notices are separate records.** PubMed lists a retraction notice as its own record (type `Retraction Notice`). It would appear in a migraine search and must not be sent as a normal paper. The PubMed publication type for notices is `Retraction Notice` (8 migraine papers all-time); my first query used "Retraction of Publication", which returns 0 (corrected 2026-10-30, see `Window_filter_tests.md`).
* **Decision 2026-10-30 (owner):** retracted papers are **kept and flagged separately** in the newsletter (and in the other outputs), not silently dropped. My working interpretation: they are stored with a `retracted` flag and shown in their own clearly marked section, never mixed in with normal findings; a retraction notice is attached to its original paper rather than shown as a new paper. A notice for a paper we never sent is shown only if the original was already sent or is in the same week's results; otherwise it is stored quietly (owner confirmed 2026-10-30). Detection rule below still applies.
* **Detection rule:** flag a paper as retracted if **any** of PubMed type, Crossref `updated-by`, or OpenAlex `is_retracted` says so; drop notice records from the digest (or attach them to the original). Check at digest-build time, and re-check items sent in the last few weeks, because retractions arrive after publication.
* **Not tested:** how fast a brand-new retraction shows up in each source; OpenAlex batch lookups by DOI list (`doi:a|b|c`) `[UNVERIFIED]`; Crossref's separate Retraction Watch CSV download. The sample is 18 papers, one topic, one day.

### 2.12 Free full-text availability (owner requirement: printed newsletter needs full text; tested 2026-10-30)

`[LIVE]` Europe PMC, journal records first published 2026-09-30 to 2026-10-30 matching `migraine` (unfielded, so it also matches papers that only mention migraine in the body; the on-topic share is not measured), preprints excluded:

| Measure | Count |
|---|---|
| Records | 421 |
| Open access | 277 (66%) |
| Full text in PMC (`IN_EPMC` / `HAS_FT`) | 285 |
| Preprints in the same window (`SRC:PPR`) | 18 |

* **Licences of the 277 open-access records:** CC BY 210, CC BY-NC 33, CC BY-NC-ND 32, CC BY-NC-SA 1, none listed 1. CC BY allows reproduction with attribution. NC (non-commercial) and ND (no derivatives, so no abridging or reformatting beyond verbatim copy) restrict use. Whether printing for the client is "non-commercial" is a legal question I cannot answer; needs the client or counsel.
* **34% (144 records) are not in Europe PMC's open-access set. Follow-up test below shows most of them still have a free copy elsewhere; only about 49 (12% of 421) look closed.** Those cannot be reproduced; the options are the abstract plus link, or full text obtained through the client's own subscription.
  * **Decision 2026-10-30 (owner):** for those, show as much as is openly available (abstract and metadata) and list the PDF and article links so the reader can open and print them. In the print edition each link is a scannable QR code instead of readable text (owner change 2026-10-30). Every paper gets a QR code regardless of full text or abstract-only, with a small DOI line printed under it as a misprint fallback (owner, 2026-10-30). Follow-up test below.
* **Machine-readable full text works:** `.../rest/{PMCID}/fullTextXML` returned HTTP 200 with JATS XML (~110-140 KB) for 2 of 2 tested papers.
* **Volume:** 277 open-access records a month is roughly 65 a week. Printing all of them in full would be hundreds of pages, so the print edition needs a selection limit (to be decided).
* **Not tested (before follow-up):** full text outside PMC (now partly tested below), bioRxiv/medRxiv full-text retrieval, embargo delay before PMC full text appears, how figures and tables come through for print.

**Follow-up test: do the 144 non-PMC-open-access papers have a free copy elsewhere? (2026-10-30, `[LIVE]`)**

| Source | Result |
|---|---|
| Europe PMC link list | Only 8 of 143 records list a free link. Weak for this purpose. |
| OpenAlex (batch lookup by DOI, 50 per request, 0 errors) | 136 of 138 DOIs found. **87 have a free copy** (64%), 53 of them with a direct PDF URL. Status: gold 40, hybrid 29, bronze 9, green 6, diamond 3, closed 49. |
| Unpaywall (sample of the first 40 DOIs, all HTTP 200, only an email needed) | 27 of 40 free (68%), 17 with a direct PDF URL. Agrees with OpenAlex. |

* **Overall:** 277 open-access in Europe PMC plus 87 more found by OpenAlex = about 364 of 421 (86%) have some free copy somewhere; about 49 (12%) look closed. 6 of the 144 have no DOI and 2 were not found in OpenAlex.
* **A free copy to read is not always a copy we may reproduce.** Gold, hybrid and diamond (72) normally carry a licence that must be checked per paper; bronze (9) is free to read but has no open licence, so link only; green (6) is a repository copy, often an author manuscript. I did not tally the licences.
* **Fetching the text may fail.** Publisher sites blocked scripts in the journal test (403 on 8 of 10 homepages), so downloading full text from publisher pages is probably unreliable. I did not test it. For these papers the safe default is to link, not ingest.
* **Side result:** OpenAlex batch lookup by DOI list works (the item marked unverified in 2.11), so retraction checks can be done 50 papers per request.

**Publisher mix and Crossref licence/link data for the 144 non-PMC-open-access papers (2026-10-30, `[LIVE]`, 140 had a DOI Crossref could resolve).**

| Publisher | Papers | CC licence on published version | Crossref lists a text-mining link |
|---|---|---|---|
| Springer | 29 | 14 | 29 |
| Wiley | 27 | 16 | 27 |
| Elsevier | 20 | 4 | 20 |
| Wolters Kluwer (Ovid) | 13 | 0 | 0 |
| MDPI | 9 | 9 | 0 |
| SAGE | 8 | 7 | 8 |
| Taylor & Francis (Informa) | 7 | 2 | 7 |
| Thieme | 4 | 3 | 0 |
| Frontiers | 3 | 3 | 0 |

* **Crossref can tell us the licence per paper.** 116 of 140 have a licence entry; 59 carry a Creative Commons licence on the published version: CC BY 30, CC BY-NC 23, CC BY-NC-ND 6. That settles which papers fall under the CC BY-only default without guessing.
* **135 of 140 list full-text links** (76 PDF links; 94 flagged for text mining). A listed text-mining link does **not** mean a script may fetch it: Wiley lists them for all 27 papers yet blocked all 14 of our PDF attempts. Token or subscription requirements for those links are from general knowledge and **not tested** `[UNVERIFIED]`.
* **PubMed holds no full text itself:** it gives abstracts and metadata and links out to PMC or the publisher. Full text for PubMed papers comes from PMC (already used) or the publisher.
* **Not tested with keys:** Elsevier and Wiley APIs (checked without keys below). Springer Nature was tested with a key (below).
* **Springer Nature Open Access API (checked 2026-10-30):** `api.springernature.com` answers (HTTP 401, "API key is invalid or missing") when called with no or an invalid key, so the service is reachable and key-gated `[LIVE]`. Terms from a web search of the developer portal, **not confirmed by me because the official pages did not render**: free tier about 500 hits/day and 100 hits/minute with one key; a JATS full-text endpoint; free for **non-commercial** use `[UNVERIFIED]`. **Key created by the owner and stored in `.env` (2026-10-30); tested `[LIVE]`.** Request used: `GET https://api.springernature.com/openaccess/jats?q=doi:{doi}&api_key=...`. On the 28 Springer (`10.1007/`) and BMC (`10.1186/`) DOIs in the 144-paper non-PMC sample: **14 returned full-text JATS XML (HTTP 200, with a `<body>`), 14 returned 404 "No data was found"** (all 14 are `10.1007/`, which fits subscription-only papers; I did not match them one by one). Licences of the 14: CC BY-NC 9, CC BY 4, CC BY-NC-ND 1. Body length: median about 6,500 words, range 80 to 12,960; 12 of 14 have 1,500+ words (the 80-word one is probably a notice). Each response carried the licence URL inside the XML. The rate-limit headers were not returned, so the 500/day and 100/minute figures remain unconfirmed. Whether the project counts as non-commercial under the terms is for the owner/client to judge.
* **Comparison:** the API returned full text for 14 papers where the earlier PDF route downloaded 8 Springer PDFs from a sample of 30, and it gives structured XML (cleaner than PDF text) plus the licence in the same response.

**Elsevier and Wiley APIs, checked without keys (2026-10-30).**

| | Elsevier (ScienceDirect Article Retrieval) | Wiley (TDM API) |
|---|---|---|
| Host reachable | Yes, `api.elsevier.com` HTTP 200 `[LIVE]` | Yes, `api.wiley.com` answered HTTP 400 with an empty body when no token was sent `[LIVE]` |
| Without a key | Returns only a title and metadata (2 KB, 39 words, no body text); the next request got HTTP 429 `RATE_LIMIT_EXCEEDED` `[LIVE]` | Nothing returned `[LIVE]` |
| What you need | A free API key from `dev.elsevier.com` (register for an Elsevier user ID); free for non-commercial use per the support page | A Wiley TDM token: log in as a Wiley user and accept a click-through licence; Wiley says to check first whether your library or institution has a separate TDM agreement (from a web search; Wiley's own page returned 403 to my script, so unconfirmed) |
| Limits | Official table: Article Retrieval 50,000 requests per week, 10 per second, unlimited weekly quota for text-mining keys `[DOCS]` | Reported 3 requests/second; a third-party tool also assumes 60 per 10 minutes `[UNVERIFIED]` |
| Content | Key alone does not give content: the docs say new keys need a verified **entitlement** (a subscribing institution); open-access articles should be retrievable `[UNVERIFIED]` | Full-text **PDF** by DOI, not XML `[UNVERIFIED, secondary sources]` |
| Likely yield | Only 4 of Elsevier's 20 sampled papers carry a CC licence, so about 4 a month of free full text | Wiley lists 27 sampled papers, 16 with a CC licence; whether the token covers open-access papers that currently get blocked is not known |

* **Elsevier incidentally flags retractions:** the keyless response's title began "RETRACTED:" and the `openaccess` field was `1`, so even the free metadata call carries a retraction hint for Elsevier papers (single example).
* **Neither is tested with a key.** Each needs the owner to register (account, email verification, terms), and both free tiers are conditioned on non-commercial or institutional use, so the owner or client should judge that first.

**Does it matter for the client's own 10 journals? (2026-08-01 to 2026-10-30, `[LIVE]`)** The broad sample above counted any paper mentioning migraine; this counts migraine in title or abstract, in the 10 journals only.

| Journal (publisher) | Migraine papers | Open in PMC | OpenAlex open-access status |
|---|---|---|---|
| Headache (Wiley) | 41-42 | 4 | hybrid 20, bronze 9, closed 12, green 1 |
| Cephalalgia (SAGE) | 24 | 0 | hybrid 24 |
| Frontiers in Neurology | 45 | 38 | - |
| J Headache Pain (Springer/BMC) | 8 | 5 | - |
| Neurology (Wolters Kluwer) | 8 | 0 | closed 5, bronze 3 |
| Nutrients (MDPI) | 2 | 2 | - |
| Lancet Neurology, JAMA Neurology, Brain Behav Immun | 1 each | 0 | Lancet closed 1, JAMA green 1 |
| Redox Biology | 0 | 0 | - |
| **All 10** | **131** | **49** | |

* **The two flagship migraine journals are the awkward ones.** Headache and Cephalalgia are 65 of the 131 papers, and only 4 are in PMC, yet most are open access at the publisher (hybrid). Script access fails for both: all 6 Headache PDF downloads tried got HTTP 403, and OpenAlex lists **no direct PDF link for any of Cephalalgia's 24** (landing pages only), so SAGE is link-only for now. I know of no SAGE API route `[UNVERIFIED]`.
* **Elsevier is negligible here:** Lancet Neurology and Brain Behav Immun had 1 migraine paper each in three months and Redox Biology had none. An Elsevier key is not worth the effort.
* **Decision 2026-10-30 (owner): the Wiley token and the Elsevier key are both dropped.**
* **Wiley is the only route with real upside:** roughly 20 to 29 Headache papers in three months (about 2 a week) are free to read but blocked for scripts. Whether a Wiley TDM token returns them is untested. Their licences (CC BY or NC) are also unchecked.

**PDF download and text extraction test (2026-10-30, `[LIVE]`).** OpenAlex gave a direct PDF URL for 53 of the 144. I tried the first 30 (not a random sample) with an honest user-agent, no block evasion, then ran `pdftotext` on what came back.

| Outcome | Count |
|---|---|
| Real PDF downloaded | 13 |
| HTTP 403 (blocked) | 16 (14 of them on `onlinelibrary.wiley.com`, even where the paper is open access) |
| Connection error | 1 |

* **The 13 downloaded PDFs all yielded text.** 11 have 1,500+ words (median 15 pages). One is a one-page correction notice, one is a 2-page short item. Mostly Springer (8), plus Frontiers, BJGP Open, Dove Press and Spandidos. Licences on these 13: CC BY 3, CC BY-NC 7, CC BY-NC-ND 2, none listed 1 (the correction).
* **Reachability depends on the publisher:** Springer, Frontiers, Dove Press, BJGP Open and Spandidos served PDFs to a script; Wiley blocked it every time (14 of 14). Where a paper is in PMC, Europe PMC full-text XML avoids this problem (section above).
* **Text quality was only partly checked.** I read the first ~110 characters and counted words, abstract and reference headings. Two PDFs (Frontiers, Dove Press) started with blank/header-only text, so the reading order, two-column layout, tables and figure captions are **not** verified. Extracted PDF text is plain text only; figures would be lost.
* The test PDFs sit in the scratchpad only, are not part of the deliverables and are not redistributed.

**Date and category filter validity inside the 3-day window: see `Window_filter_tests.md`** (index-date filters exact; publication-date filters miss most new records; OpenAlex created/updated filters need a paid plan).

## 3. Search Subjects dictionary: probe results

PubMed title/abstract counts, all-time and last 30 days (entry date). `+ migraine` = AND `migraine[tiab]`. All `[LIVE]`, 2026-10-30. This shows which terms need a migraine gate.

| Sheet row | Term | Alone | + migraine | + migraine, last 30 d |
|-----------|------|------:|-----------:|----------------------:|
| A1 | migraine | 49,080 | 49,080 | 237 |
| A1 | chronic migraine | 4,411 | 4,411 | 45 |
| A1 | episodic migraine | 2,184 | 2,184 | 19 |
| A1 | migraine with aura | 3,119 | 3,119 | 12 |
| A2 | CGRP | 13,607 | 3,133 | 42 |
| A2 | PACAP | 4,024 | 248 | 2 |
| A2 | trigeminovascular | 1,140 | 955 | 11 |
| A2 | meningeal nociception | 46 | 33 | 0 |
| A3 | cortical spreading depression | 1,609 | 903 | 5 |
| A3 | cortical spreading depolarization | 255 | 129 | 1 |
| A3 | **CSD** | 5,632 | 495 (9%) | 2 |
| A4 | prophylaxis | 133,689 | 2,301 | 9 |
| A4 | randomized trial | 65,429 | 135 | 0 |
| B6 | mitochondrial dysfunction | 49,579 | 129 | 1 |
| B7 | oxidative stress | 363,862 | 340 | 4 |
| B7 | glutathione | 180,399 | 60 | 0 |
| B7 | microglia | 60,976 | 141 | 4 |
| B7 | neuroinflammation | 61,873 | 361 | 12 |
| B8 | riboflavin | 13,872 | 134 | 0 |
| B8 | magnesium | 82,015 | 331 | 1 |
| B8 | CoQ10 | 3,471 | 31 | 1 |
| B8 | feverfew | 356 | 99 | 0 |
| B8 | parthenolide | 1,151 | 36 | 0 |
| B9 | estradiol | 97,609 | 155 | 2 |
| B9 | menstrual migraine | 478 | 478 | 1 |
| B10 | microbiome | 103,060 | 73 | 0 |
| B10 | kynurenine | 8,912 | 67 | 0 |
| B10 | gut-brain axis | 10,700 | 106 | 3 |
| B11 | insulin resistance | 121,750 | 61 | 1 |
| B11 | endothelial dysfunction | 40,116 | 126 | 3 |
| B12 | CACNA1A | 1,042 | 443 | 0 |
| B12 | ATP1A2 | 431 | 254 | 0 |
| B12 | SCN1A | 1,853 | 148 | 1 |
| B12 | GWAS | 38,418 | 190 | 1 |

Terms not run: generic words such as `prevention`, `placebo`, `disability`, `sleep`, `mood`, `ATP`, `OXPHOS`, `NAD`, `SCFA`, `eNOS`, `glucose`, `estrogen`, `B vitamins`, `ion channels` and `endocrine`. Those are expected to be as noisy as the other generic terms.

**Findings**
* **"CSD" is as ambiguous as the sheet warns:** 5,632 hits, only 9% mention migraine; use the full phrases and require migraine for the abbreviation.
* **Track A words are high-precision**; **Track B words are not.** `oxidative stress` (363,862) and `randomized trial` (65,429) are useless without a migraine gate; the sheet already requires one.
* `prophylaxis` and `randomized trial` are generic: only 2% and 0.2% of hits carry migraine, matching the sheet's note to require migraine context.
* **Expected daily/weekly load, direct path:** about 237 PubMed items per 30 days with `migraine` in title/abstract (roughly 55 per week). The Track B terms add only 0-12 each per 30 days after the migraine gate.
* **The migraine gate will drop the interesting indirect papers**: a Track B paper about glutathione without the word migraine has no way into the digest under the sheet's own rules. **Decision 2026-10-30 (provisional):** "curated mechanistic bridge" is treated as a short human-maintained list of specific mechanism-to-migraine links approved by the client; Track B papers without the word migraine are admitted only through that list and labelled exploratory. Generic terms are never allowed alone.

## 4. Cross-cutting issues
1. **Rate budget for a daily run:** PubMed (3/s) and Crossref (1/s lists) are comfortable for a few hundred calls per day. OpenAlex is the constraint without a key.
2. **Two sources are not scriptable:** Google Scholar (no API) and Cochrane (blocked).
3. **bioRxiv/medRxiv volume is high and unfiltered** at source; consider Europe PMC for keyword preprint search.
4. **Freshness fields differ:** PubMed `edat` vs `dp`; Crossref created vs indexed; Europe PMC `FIRST_PDATE`. Store both indexed and publication dates (matches the sheet).
5. **Terms of use** for Crossref, PubMed, Europe PMC and ClinicalTrials.gov are standard open-data terms, not reviewed in detail here `[UNVERIFIED]`.

## 5. Not verified
* Behaviour with an NCBI API key (not created); OpenAlex with a key and paid pricing; Crossref polite-pool headers.
* Europe PMC and bioRxiv numeric rate limits; ClinicalTrials.gov numeric limit.
* Any keyword filter on the bioRxiv/medRxiv API.
* Google Scholar blocking behaviour at volume (deliberately not tested).
