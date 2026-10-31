# Journal Source Validation

Source: `Akeso Migraine Monitoring System Sources - Adnan.xlsx`, sheet **Agent - Journals** (10 journals).
Validated: **2026-10-30**, from a developer laptop IP, using read-only GET requests only (no accounts, no API keys, no payments).

**Evidence tags:** `[LIVE]` observed in a request during this validation; `[DOCS]` stated in provider docs/response text, not independently tested; `[UNVERIFIED]` from general knowledge, must be confirmed before relying on it.

## 1. Summary

| # | Journal | Track | ISSN (print / online) | Publisher (Crossref) | Homepage to a script | Machine feed that works | PubMed-indexed |
|---|---------|-------|----------------------|----------------------|----------------------|-------------------------|----------------|
| 1 | Cephalalgia | A / Core | 0333-1024 / 1468-2982 | SAGE | **403** | RSS (RDF) 200 | Yes |
| 2 | Headache | A / Core | 0017-8748 / 1526-4610 | Wiley | **403** | RSS 200 | Yes |
| 3 | J Headache & Pain | A / Core | 1129-2369 / 1129-2377 | Springer (BMC site redirects to link.springer.com) | 200 (slow, ~5 s, cookie redirect) | Springer search RSS 200; BMC-style RSS URL returns HTML, not a feed | Yes |
| 4 | Neurology | A / Core | 0028-3878 / 1526-632X | Wolters Kluwer / AAN | **403** | RSS (RDF) 200 | Yes |
| 5 | Lancet Neurology | A / Core | 1474-4422 / (online ISSN not returned) | Elsevier | **403** | RSS 200 | Yes |
| 6 | JAMA Neurology | A / Core | 2168-6149 / (online ISSN not returned) | AMA | **403** | **404** on guessed URL; RSS landing page exists (feed URL not resolved) | Yes |
| 7 | Frontiers in Neurology | A+B | 1664-2295 | Frontiers | 200 | RSS 200 | Yes |
| 8 | Brain, Behavior, and Immunity | B | 0889-1591 / 1090-2139 | Elsevier | **403** | RSS 200 (via rss.sciencedirect.com) | Yes |
| 9 | Redox Biology | B | 2213-2317 | Elsevier | **403** | RSS 200 (via rss.sciencedirect.com) | Yes |
| 10 | Nutrients | B | 2072-6643 | MDPI | **403** | **403 Access Denied** (also `/rss`) | Yes |

`[LIVE]` for every cell above except where noted. Journal titles in the sheet all resolve to a real Crossref journal record with a matching ISSN. Note the sheet title "Brain, Behavior, and Immunity" is "Brain Behavior and Immunity" in Crossref, and "Headache: The Journal of Head and Face Pain" is "Headache The Journal of Head and Face Pain" (colon dropped): store title aliases and match on ISSN.

## 2. Reachability findings

* **8 of 10 publisher homepages return HTTP 403** to a plain scripted GET (Cloudflare/bot protection) `[LIVE]`. Only J Headache Pain (200) and Frontiers (200) answered. Do not plan on scraping journal websites or issue pages.
* **8 of 11 RSS/TOC URLs tried return a real feed** `[LIVE]` (Cephalalgia, Headache, Springer-hosted JHP, Neurology, Lancet Neurology, Frontiers, BBI, Redox Biology). These are the reliable publisher-side feeds.
* **Gaps:** JAMA Neurology (feed URL not found; the journal's RSS page loads, the URL pattern I guessed returned 404), Nutrients/MDPI (feed blocked, `Access Denied`), and J Headache Pain's own BMC-style feed (returns HTML). Fall back to the indexes below for these.
* I did not send browser-spoofed headers to get around the 403s. Whether the publishers' terms allow automated TOC retrieval is `[UNVERIFIED]`.
* RSS tends to carry the latest issue/online-first items only; it is a gap-check source, not a complete history (consistent with the sheet's own description in Discovery row 8).

## 3. Index coverage: the reliable alternative

Because the websites block scripts, the identity and the article stream for all 10 journals can be taken from open indexes using the ISSN. All counts are `[LIVE]`, 2026-10-30.

| Journal | PubMed total | PubMed `migraine[tiab]` all-time | 2025-26 | last 30 days (entry date) | Crossref DOI total |
|---------|-------------|------------------|---------|-------------|--------------------|
| Cephalalgia | 6,224 | 3,815 | 268 | 6 | 8,303 |
| Headache | 8,231 | 4,622 | 340 | 16 | 10,366 |
| J Headache Pain | 3,041 | 2,108 | 412 | 9 | 4,192 |
| Neurology | 47,101 | 1,014 | 40 | 5 | 97,116 |
| Lancet Neurology | 6,006 | 131 | 13 | 0 | 7,453 |
| JAMA Neurology | 3,924 | 52 | 10 | 1 | 4,258 |
| Frontiers in Neurology | 21,976 | 720 | 204 | 16 | 22,221 |
| Brain Behav Immun | 6,384 | 9 | 3 | 0 | 10,084 |
| Redox Biology | 4,384 | **0** | **0** | 0 | 4,368 |
| Nutrients | 39,732 | 66 | 18 | 0 | 39,582 |

**What this means for the sheet's design**
* Core headache journals (Cephalalgia, Headache, JHP) carry the bulk of direct migraine volume (roughly 6-16 per month each).
* Track B journals are very low-yield for the word "migraine": **Redox Biology has zero migraine title/abstract hits ever**, Brain Behav Immun has 9, Nutrients 66. For these journals the value only comes from the Track B subject dictionary (mechanism terms), not from the journal filter alone. Expect most of their items to be rejected by the "explicit migraine-relevant connection" gate.
* Neurology, Lancet Neurology, JAMA Neurology are broad journals: a subject filter is mandatory (47k articles in Neurology, ~1k migraine-related).

## 4. Usage limits and pricing

| Resource | Auth | Limit / cost | Evidence |
|----------|------|--------------|----------|
| Publisher RSS feeds | none | No numeric limits published that I found. Be conservative (a few requests per feed per day). | `[UNVERIFIED]` |
| Publisher websites | n/a | Blocked to scripts (403). Subscription content paywalled for Neurology, Lancet Neurol, JAMA Neurol, Elsevier subscription titles. | `[LIVE]` for 403; paywall `[UNVERIFIED]` |
| Crossref per-journal endpoint | none | Observed `x-rate-limit-limit: 5 / 1s` on `/journals/{issn}` and `1 / 1s` on `/works` list queries (public pool). Docs describe a faster "polite" pool when a contact email is supplied. | observed `[LIVE]`; polite pool `[DOCS]` |
| PubMed E-utilities | none (key optional) | `X-Ratelimit-Limit: 3` per second without a key `[LIVE]`; 10/s with a free key `[DOCS]` | see Discovery_API_check.md |
| DOAJ search API | none | Used for APC lookup; no limit observed in light use | `[LIVE]` |

**Open access and article processing charges (APC), via DOAJ `[LIVE]`**

| Journal | OA model | APC listed |
|---------|----------|-----------|
| Cephalalgia | Subscription/hybrid (not in DOAJ) | n/a |
| Headache | Subscription/hybrid (not in DOAJ) | n/a |
| J Headache Pain | Fully OA (in DOAJ) | EUR 3,290 / USD 4,190 / GBP 2,990 |
| Neurology | Subscription/hybrid (not in DOAJ) | n/a |
| Lancet Neurology | Subscription/hybrid (not in DOAJ) | n/a |
| JAMA Neurology | Subscription/hybrid (not in DOAJ) | n/a |
| Frontiers in Neurology | Fully OA (in DOAJ) | CHF 3,150 |
| Brain Behav Immun | Subscription/hybrid (not in DOAJ) | n/a |
| Redox Biology | Fully OA (in DOAJ) | USD 4,700 |
| Nutrients | Fully OA by MDPI's model `[UNVERIFIED]`: **DOAJ returned no record**, which is unexpected and was not investigated | n/a |

APCs are the cost to authors, not to this project. **Reading the abstracts and metadata through PubMed/Crossref/Europe PMC is free.** Full text is only freely available for the OA journals and some PMC-deposited papers.

## 5. Issues and recommendations

1. **Query every ISSN of a journal.** On 2026-10-30 Crossref's per-journal route returned 0 recent migraine papers for the Journal of Headache and Pain under its print ISSN (1129-2369) but 9 under its online ISSN (1129-2377); see `Window_filter_tests.md`.
1. **Use ISSN as the canonical journal key** and PubMed/Crossref as the article source of truth; use publisher RSS only as an extra online-first gap check for the 8 feeds that work.
2. **Resolve the three feed gaps** (JAMA Neurology, Nutrients, JHP BMC-style feed) with the client or by finding official feed URLs. MDPI explicitly denied access to a scripted client.
3. **Crossref "from-index-date" counts are not new articles** (e.g. Neurology showed ~11k records indexed since Oct 1, mostly metadata updates). Use `from-pub-date`/`from-created-date` for freshness windows, and treat any "indexed since" number from this run as invalid for volume planning.
4. Ask the client whether Track B journals should stay in the *journal* path at all, given Redox Biology = 0 migraine papers; they may be better served only through the subject-term path.
5. Confirm publisher terms (SAGE, Wiley, Elsevier, AMA, Wolters Kluwer, MDPI) before any full-text retrieval beyond metadata and abstracts `[UNVERIFIED]`.

## 6. Not verified in this pass
* Impact factors / journal ranking (the sheet says it is explicitly not a ranking).
* Online ISSN for Lancet Neurology and JAMA Neurology (Crossref returned print only).
* JAMA Neurology RSS URL; MDPI feed access from an authorized client; publisher TDM terms.
* The sheet's URLs for Frontiers and Springer-hosted JHP resolve fine; the `https://www.sciencedirect.com/...` URLs for BBI and Redox Biology return 403 even though the same publisher's RSS host works.
