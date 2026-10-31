# Researchers Source Validation

Source: `Akeso Migraine Monitoring System Sources - Adnan.xlsx`, sheet **Agent - Researchers** (30 researchers).
Validated: **2026-10-30**, read-only GET requests, no accounts/keys. Evidence tags: `[LIVE]` observed in this run; `[DOCS]` provider documentation; `[UNVERIFIED]` not checked.

## 1. Headline finding: the sheet's PubMed links return nothing

Every researcher link in the sheet uses the form `https://pubmed.ncbi.nlm.nih.gov/?term="Richard B. Lipton"[Author]`.

* Through the E-utilities API, **all 30 queries in that form return 0 results** `[LIVE]` (translated to `"richard b lipton"[Author]`, "No items found").
* The PubMed web page for the same URL also shows its no-results marker for the three names I opened (Lipton, Goadsby, M. Ashina) `[LIVE]`. I did not open all 30 pages.
* PubMed indexes authors as **Surname + initials** (`Lipton RB`). Working forms: `"Lipton RB"[Author]` returned 1,281 papers; unquoted `Richard B. Lipton[Author]` returned 937 (PubMed expands it to two name orders); the exact full-name field (`[FAU]`) only works as `"Lipton, Richard B"[FAU]` (935).
* **Action:** do not pass the sheet's URLs to the agent as written. Regenerate queries from the resolved identity (ORCID/OpenAlex ID, with PubMed `Surname Initials` as a fallback).

The sheet itself says name-only links are not verified identity. The data below confirms that and shows how ambiguous some are.

**Owner confirmation (2026-10-30): all 30 profiles were reviewed by the project owner against `Researchers_recent_papers.pdf` and found correct.** Statuses below describe the automated evidence; the owner review supersedes the "not yet human-verified" wording.

## 2. Method
For each researcher: (a) PubMed count for `"Surname Initials"[Author]` overall, with `migraine[tiab]`, 2024-2026, and last 30 days (entry date); (b) ORCID public search (`pub.orcid.org`, no key) by given/family name; (c) OpenAlex author search, top 3 candidates. "Agree" = the ORCID iD on the top OpenAlex candidate is also among the ORCID search results. Third-party email addresses returned by ORCID were deliberately **not** recorded.

**This is name matching plus two corroborating indexes, not identity verification.** A "strong candidate" still needs a human or an authoritative source (e.g. the researcher's institutional page) before it is stored as truth. Counts are PubMed Title/Abstract mentions of "migraine", so non-migraine headache work is excluded from the migraine columns.

Initials were derived from each name (e.g. Else Tolner to `Tolner EA`, Andrea Harriott to `Harriott AM`) and the three non-obvious ones came from ORCID/OpenAlex (E. Anne MacGregor, Else A. Tolner, Andrea M. Harriott). These derived initials are assumptions.

## 3. Results

| # | Researcher | Track | Working PubMed query | PubMed all | with migraine (share) | migraine 2024-26 | last 30 d | OpenAlex ID (top hit) | ORCID (from OpenAlex) | Sources agree | Status / notes |
|---|-----------|-------|------|-----|------|-----|----|----------|----------|----|------|
| 1 | Richard B. Lipton | A | `"Lipton RB"[Au]` | 1,281 | 726 (57%) | 107 | 4 | A5000448269 | 0000-0003-2652-2897 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 2 | Peter J. Goadsby | A | `"Goadsby PJ"[Au]` | 927 | 630 (68%) | 65 | 0 | A5004525209 | 0000-0003-3260-5904 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 3 | Messoud Ashina | A | `"Ashina M"[Au]` | 521 | 389 (75%) | 91 | 2 | A5023775224 | 0000-0003-0951-5804 | yes | Strong candidate (ORCID + OpenAlex agree). Shares surname with Håkan Ashina; use the initials `Ashina M` vs `Ashina H`. |
| 4 | Michel D. Ferrari | A | `"Ferrari MD"[Au]` | 535 | 367 (69%) | 7 | 0 | A5039941295 | 0000-0001-9691-9449 | yes | Strong candidate (ORCID + OpenAlex agree). 4 ORCID matches incl. unrelated people; the OpenAlex ORCID (…9691-9449) matches the 'Michel D.' record. Only 7 migraine papers 2024-26 in PubMed. |
| 5 | Stephen D. Silberstein | A | `"Silberstein SD"[Au]` | 439 | 336 (77%) | 10 | 0 | A5060976552 | 0000-0001-9467-5567 | yes | Resolved candidate after follow-up (not yet human-verified). 100 migraine works in OpenAlex (10 in 2024-26); ORCID employer Thomas Jefferson Univ. **A second OpenAlex record A5108076630 (667 works, no ORCID) is a duplicate: store both IDs.** |
| 6 | David W. Dodick | A | `"Dodick DW"[Au]` | 514 | 320 (62%) | 39 | 0 | A5057290642 | 0000-0002-9486-6790 | yes | Strong candidate (ORCID + OpenAlex agree). 3 ORCID records for 'David Dodick' (one is probably an unrelated student/other); OpenAlex ORCID is the Mayo record. |
| 7 | Todd J. Schwedt | A | `"Schwedt TJ"[Au]` | 232 | 176 (76%) | 45 | 1 | A5019480561 | 0000-0002-7780-7086 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 8 | Patricia Pozo-Rosich | A | `"Pozo-Rosich P"[Au]` | 264 | 213 (81%) | 89 | 2 | A5041516784 | 0000-0003-0796-4702 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 9 | Cristina Tassorelli | A | `"Tassorelli C"[Au]` | 530 | 273 (52%) | 79 | 3 | A5053749762 | 0000-0003-1513-2113 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 10 | Jessica Ailani | A | `"Ailani J"[Au]` | 120 | 109 (91%) | 42 | 1 | A5057037500 | 0000-0001-8106-4927 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 11 | Dawn C. Buse | A | `"Buse DC"[Au]` | 248 | 227 (92%) | 48 | 1 | A5024044218 | 0000-0002-3784-6144 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 12 | Amy A. Gelfand | A | `"Gelfand AA"[Au]` | 126 | 82 (65%) | 12 | 0 | A5076177073 | 0000-0002-0911-7905 | yes | Strong candidate (ORCID + OpenAlex agree). OpenAlex shows UCSF; ORCID shows Harvard (older). |
| 13 | Christina L. Szperka | A | `"Szperka CL"[Au]` | 78 | 50 (64%) | 21 | 1 | A5065625930 | 0000-0003-4786-8889 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 14 | Shuu-Jiun Wang | A | `"Wang SJ"[Au]` | 3,661 | 264 (7%) | 49 | 2 | A5028390017 | 0000-0001-5179-5358 | yes | Resolved candidate after follow-up (not yet human-verified). 287 migraine works (46 in 2024-26); ORCID employers Taipei Veterans General Hospital and NYCU. Ambiguous in PubMed, so **always search by ORCID/OpenAlex ID, not `Wang SJ`**. |
| 15 | Nazia Karsan | A | `"Karsan N"[Au]` | 52 | 41 (79%) | 11 | 0 | A5014589327 | 0000-0002-6946-5637 | yes | Strong candidate (ORCID + OpenAlex agree). 2 ORCID records; OpenAlex's is the King's College London one. |
| 16 | Andrew Charles | A + B | `"Charles A"[Au]` | 675 | 56 (8%) | 7 | 0 | A5058179140 | 0000-0003-3379-054X | yes | Resolved candidate after follow-up (not yet human-verified). OpenAlex *Andrew C. Charles*, UCLA, 96 migraine works (7 in 2024-26). First-pass top hit (oncology author, 0 migraine works) was the wrong person and is rejected. |
| 17 | Rami Burstein | A + B | `"Burstein R"[Au]` | 332 | 175 (53%) | 22 | 0 | A5016966448 | 0000-0003-3763-8916 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 18 | Andrew F. Russo | A + B | `"Russo AF"[Au]` | 137 | 75 (55%) | 13 | 0 | A5071785183 | 0000-0002-8156-5649 | yes | Resolved candidate after follow-up (not yet human-verified). ORCID record is at Univ. of Iowa; 101 migraine works (13 in 2024-26). The other two 'Andrew Russo' ORCID candidates have 0 migraine works. |
| 19 | Amynah A. Pradhan | A + B | `"Pradhan AA"[Au]` | 78 | 32 (41%) | 9 | 0 | A5065566445 | 0000-0001-9691-2976 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 20 | Gregory Dussor | A + B | `"Dussor G"[Au]` | 139 | 47 (34%) | 6 | 0 | A5056024832 | 0000-0002-1193-9238 | yes | Resolved candidate after follow-up (not yet human-verified). ORCID record is named *Greg Dussor* (UT Dallas), which is why the 'Gregory' search found nothing. 61 migraine works (10 in 2024-26). Store alias `Greg`. |
| 21 | Debbie L. Hay | B | `"Hay DL"[Au]` | 203 | 53 (26%) | 11 | 1 | A5020334174 | 0000-0002-9558-5122 | yes | Strong candidate (ORCID + OpenAlex agree). Pharmacologist: only 26% of her papers mention migraine, consistent with Track B only. OpenAlex lists her at Otago; ORCID lists Imperial (different snapshots). |
| 22 | Michael L. Oshinsky | A + B | `"Oshinsky ML"[Au]` | 40 | 17 (42%) | 2 | 0 | A5029958480 | 0000-0002-0976-1047 | yes | Strong candidate (ORCID + OpenAlex agree). Small footprint (40 PubMed papers). ORCID affiliation = American Headache Society. |
| 23 | Else Tolner | B | `"Tolner EA"[Au]` | 65 | 28 (43%) | 6 | 0 | A5090031681 | 0000-0002-6501-9971 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 24 | Arn M. J. M. van den Maagdenberg | B | `"van den Maagdenberg AM"[Au]` | 224 | 136 (61%) | 1 | 0 | A5012664216 | 0000-0002-9310-5535 | yes | Strong candidate (ORCID + OpenAlex agree). Only 1 migraine paper 2024-26 under `van den Maagdenberg AM`; ORCID now shows an industry affiliation. Check name-variant (`van den Maagdenberg A`). |
| 25 | K. C. Brennan | A + B | `"Brennan KC"[Au]` | 78 | 51 (65%) | 5 | 0 | A5009414463 | 0000-0003-4193-5841 | yes | Resolved candidate after follow-up (not yet human-verified). ORCID *K.C. Brennan*, employer Texas A&M Health Science Center; 63 migraine works (5 in 2024-26). OpenAlex lists no institution, so **affiliation is single-sourced**. |
| 26 | Andrea Harriott | A + B | `"Harriott AM"[Au]` | 43 | 28 (65%) | 9 | 0 | A5057175211 | 0000-0003-0417-7586 | yes | Strong candidate (ORCID + OpenAlex agree).  |
| 27 | Anne MacGregor | A + B | `"MacGregor EA"[Au]` | 140 | 105 (75%) | 4 | 0 | A5020644806 | 0000-0002-4116-9828 | yes | Strong candidate (ORCID + OpenAlex agree). Sheet name is a variant: ORCID/OpenAlex show **E. Anne MacGregor** (Barts Health, 0000-0002-4116-9828); PubMed `MacGregor EA` used here. A second 'Anne Macgregor' (Nottingham) exists. |
| 28 | Jan Hoffmann | A + B | `"Hoffmann J"[Au]` | 1,531 | 46 (3%) | 10 | 0 | A5008504650 | 0000-0002-2103-9081 | yes | Resolved candidate after follow-up (not yet human-verified). OpenAlex lists King's College London, 61 migraine works (13 in 2024-26). The ORCID record shows **no public employer**, so affiliation is single-sourced. Other 'Jan Hoffmann' ORCIDs checked (Bern, Frankfurt) have 0 migraine works. |
| 29 | Simon Akerman | A + B | `"Akerman S"[Au]` | 210 | 79 (38%) | 8 | 0 | A5041491240 | 0000-0002-6577-6825 | yes | Strong candidate (ORCID + OpenAlex agree). 2 ORCID records; OpenAlex's ORCID is the Univ. of Maryland record. |
| 30 | Håkan Ashina | A | `"Ashina H"[Au]` | 118 | 98 (83%) | 60 | 1 | A5085289733 | 0000-0002-6268-727X | yes | Strong candidate (ORCID + OpenAlex agree). ORCID affiliation field shows a non-academic entity ('Berlingske'); OpenAlex shows Univ. of Copenhagen. Confirm. Sheet name uses a diacritic; PubMed matched `Ashina H`. |

## 4. Summary
* **All 30 now have a resolved candidate** (ORCID + OpenAlex ID). 23 were resolved in the first pass; the other 7 were resolved in the 2026-10-30 follow-up (section 7). None is human-verified yet.
* **Name-collision risk remains for Hoffmann, Wang and Charles in PubMed** (3-8% of their `Surname Initials` hits are migraine), so search them by ORCID/OpenAlex ID, never by name.
* Even the strongest candidates have small anomalies listed in the notes (e.g. conflicting affiliations between ORCID and OpenAlex snapshots, duplicate OpenAlex records). All 30 should be reviewed once by a human before identity-based search goes live.
* **Activity:** only **11 of 30** researchers had a migraine paper in the last 30 days (entry date), **19 papers in total** across all 30 (max 4 for one person) `[LIVE]`. Author monitoring will add few items per week; these may overlap the journal path, so dedup will be needed.
* **Possible dormancy / name-variant gaps:** Ferrari (7 migraine papers 2024-26), Silberstein (10), van den Maagdenberg (1), MacGregor (4). Test name variants before concluding they are inactive.
* Sheet-name variances: "Anne MacGregor" is **E. Anne MacGregor**; Else Tolner is Else A. Tolner; Håkan Ashina has a diacritic (PubMed folded it correctly for `Ashina H`).

## 5. Usage limits and pricing for the services used here

| Service | Auth | Limit | Price | Evidence |
|---------|------|-------|-------|----------|
| PubMed E-utilities | none | 3 requests/second without a key (header `X-Ratelimit-Limit: 3`); 10/s with a free key | free | 3/s `[LIVE]`; 10/s with key `[DOCS]` |
| ORCID public API (`pub.orcid.org/v3.0`) | none for public search | no limit headers observed; ORCID documents public-API rate limits, numbers not verified | free | works `[LIVE]`; limits `[UNVERIFIED]` |
| OpenAlex | key recommended | Without a key: one shared **$0.10/day** budget per IP; each request cost about **$0.001 (10 credits of 1,000)** in this run. A free key is stated to give **$1/day** | free tier shown; paid tiers/prepaid exist (`X-RateLimit-Prepaid-Remaining-USD` header) | `[LIVE]` from response headers and notice |

**OpenAlex key:** the response suggests an agent can create a key with `POST /api-keys` using the user's email. I did not do this: it would register an email address with a third party without your authorization. This is a decision for the account owner (see Progress.md, open decisions).

At 30 authors, a daily OpenAlex sweep costs roughly $0.03 and the daily budget covers it, but only if the budget is not shared with other users on the same IP (it is shared per IP without a key).

## 6. Recommendations
1. Replace the sheet's name-only URLs with stored identities: `{orcid, openalex_id, pubmed_query, aliases, affiliation, last_verified}`.
2. Human-confirm all 30 once. Priority: Hoffmann and Brennan (affiliation single-sourced), Silberstein (duplicate OpenAlex record).
3. Use author queries as an OR path next to journal and subject paths. Hoffmann, Wang and Charles must always be combined with ORCID or `migraine`/journal constraints, otherwise they will flood the digest with unrelated papers.
4. ~~Re-run the Dussor and Brennan ORCID lookups~~ Done, see section 7.
5. Track which email-bearing ORCID fields we ingest; do not store personal emails that are not needed.

## 7. Follow-up (2026-10-30): flagged researchers re-checked

Method: for every ORCID iD seen for the 7 flagged names, I read the public ORCID record (name, employers; emails not recorded), found the OpenAlex author with that ORCID, and counted that author's works with `migraine` in title/abstract (all years and 2024-26) using the project OpenAlex key. Contact address for polite-pool etiquette: the project mailbox (see Progress.md).

**Why the first pass failed:** the ORCID search returns a null result list when nothing matches (my parser crashed on it) and Dussor is registered as *Greg*. Both are fixed by looking up the ORCID record directly.

| Researcher | Chosen OpenAlex ID | ORCID | Migraine works (all / 2024-26) | Rejected candidates |
|-----------|--------------------|-------|-------------------------------|---------------------|
| Stephen D. Silberstein | A5060976552 (+ duplicate A5108076630) | 0000-0001-9467-5567 | 100 / 10 | none |
| Shuu-Jiun Wang | A5028390017 | 0000-0001-5179-5358 | 287 / 46 | other Wangs in ORCID search (24 results) |
| Andrew Charles | A5058179140 | 0000-0003-3379-054X | 96 / 7 | A5110174799 (oncology, 0 migraine) |
| Andrew F. Russo | A5071785183 | 0000-0002-8156-5649 | 101 / 13 | A5045203586, A5048108003 (0 migraine) |
| Gregory Dussor | A5056024832 | 0000-0002-1193-9238 | 61 / 10 | duplicate record with 1 work |
| K. C. Brennan | A5009414463 | 0000-0003-4193-5841 | 63 / 5 | none found |
| Jan Hoffmann | A5008504650 | 0000-0002-2103-9081 | 61 / 13 | Bern and Frankfurt candidates (0 migraine) |

`[LIVE]`. Caveats: "migraine works" counts come from OpenAlex's own author-to-paper assignment, which can contain misattributions; ORCID records are self-maintained. A paper-level spot check by a human (e.g. 3-5 recent papers per person) is still the proper verification step.
