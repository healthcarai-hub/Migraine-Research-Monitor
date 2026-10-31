# Window and Filter Tests: do the date and category filters hold inside the 3-day window?

Run 2026-10-30. Read-only requests. Evidence tags: `[LIVE]` = measured today. Pipeline context is in `Pipeline_design_plan.md`; source behaviour is in `Discovery_API_check.md`.

## 1. How the test was set up

* **"Today" D = 2026-10-30 (a Friday).** The 3-day rolling window is read as the last 3 days plus today: **W0 = 27 to 30 Oct** (4 calendar days, today still partial). **W1 = 26 to 29 Oct** and **W2 = 25 to 28 Oct** are the windows the previous two daily runs would have used. **WIDE = 9 to 30 Oct** (22 days) is the reference set.
* **Date test:** for each source, pull the WIDE set with every date field the record carries, then ask of each narrow filter: does it return only records whose own date lies in the window (precision), and every such record (recall)?
* **Category test:** "category" means the 12 Track A/B categories of the Search Subjects sheet, plus the English-only, article-type, journal and author filters. Topic text checks were done by pattern matching on title and abstract.
* **Windows are small** (43 PubMed papers in W0), so percentages from a single window are rough. One day, one topic.

## 2. Date filters, source by source

| Source and filter | W0 / W1 / W2 hits | Precision / recall against its own date field | Verdict |
|---|---|---|---|
| **PubMed `edat`** (entry date) | 43 / 55 / 66 | 1.0 / 1.0 in W0 and W1. In W2, 16 extra records (precision 0.76, recall 1.0): all entered 24 Oct but carry a "pubmed" history date of 25 Oct, a one-day edge effect on a Saturday batch (cause not verified) | **Use.** Exact apart from a one-day edge effect that the overlap absorbs |
| PubMed `crdt` (creation date) | 43 / 55 / 50 | 1.0 / 1.0 in all three windows | Same as `edat` without the edge effect; also usable |
| PubMed `pdat` (publication date) | 16 / 24 / 22 | n/a | **Do not use.** Returns only 16 of the 43 new records in W0 (37%); month-only dates drop out |
| PubMed `mdat` / `mhda` | 121 / 50 in W0 | n/a | **Do not use for discovery.** `mdat` returns 2.8 times as many, 78 of them entered before the window (updates) |
| **Europe PMC `FIRST_IDATE`** (first index date) | 55 / 51 / 59 | 1.0 / 1.0 in all three; paging complete; 0 duplicates | **Use** |
| Europe PMC `CREATION_DATE` | 55 / 51 / 59 | same counts as `FIRST_IDATE` | Equivalent |
| Europe PMC `FIRST_PDATE` / `E_PDATE` | 18 / 27 / 24 | 1.0 / 1.0 | Exact but finds only 18 of the 55 new records in W0 (33%). **Do not use** |
| Europe PMC `UPDATE_DATE` | 289 / 172 / 142 | n/a | Updates, not new papers |
| **Crossref `from-created-date`** | 59 / 63 / 47 | 1.0 / 1.0; all 59 in W0 mention migraine in title or abstract | **Use** |
| Crossref `from-index-date` | 1,043 / 1,141 / 960 | 1.0 / n/a | **Do not use:** 18 times `created`; it returns re-indexed records |
| Crossref `from-deposit-date` / `from-pub-date` | 105 / 32 in W0 | 1.0 / 1.0 | Deposit includes updates; pub-date misses partial dates |
| OpenAlex `from_publication_date` | 30 / 55 / 61 | 1.0 / 1.0 | Works, but only by publication date |
| **OpenAlex `from_created_date` / `from_updated_date`** | HTTP 429 | n/a | **Not available on the free key:** "Plan upgrade required" (daily-sync filters, Member+/Partner plans) |
| ClinicalTrials.gov `StudyFirstPostDate` | 5 / 4 / 2 | precision 1.0 | **Use** (new trials) |
| ClinicalTrials.gov `LastUpdatePostDate` | 25 / 24 / 15 | precision 1.0 | Updates to existing trials |
| ClinicalTrials.gov `StudyFirstSubmitDate` | 0 / 0 / 0 | n/a | **Silent zero** in all three windows with no error: do not use, and add a "suspicious zero" alarm |
| bioRxiv / medRxiv `details` interval | bioRxiv 1,003 and medRxiv 238 records in W0 | all dates in the window; paging complete | Works, but W0 alone is about 1,240 records and 1 mentions migraine; 18 + 11 multi-version duplicates. About 90 requests for 4 windows (30 records per page) against 1 for Europe PMC `SRC:PPR`, which found the same preprint |

**Why publication dates fail** `[LIVE]`:
* PubMed: of 162 recently entered papers with a day-precise publication date, **49% (79) were entered more than 3 days after that date and 38% (61) more than 14 days after** (median 3 days, 90th percentile 40). Also 69 of 178 (39%) have only a month or year as publication date.
* Europe PMC: median gap between first publication and first index is 15 days; 73% (202 of 275) were indexed more than 3 days after publication, and 43 (16%) have a first-publication date over a year old.
* Crossref `created` tracks publication closely (median 0 days, 90th percentile 22), which is why it works as a discovery date.

**Would the weekly run make up for it?** No (checked 2026-10-30, `[LIVE]`, PubMed). 74 papers entered PubMed in the last 7 days; a publication-date search over the same 7 days finds only **29 (39%)**. The other 45 are missed: 41 were published before that week (23 of them more than 14 days earlier) and 4 have only a month or year as date. The weekly job reads from the database, so a paper no daily run fetched never reaches the newsletter. The issue is not lateness (a paper a few days late still makes the weekly edition) but papers whose publication date has already left the window by the time they appear.

**Stability:** repeating the W0 query three times gave identical counts for PubMed (43), Europe PMC (55) and Crossref (59); OpenAlex gave 31, 30, 31.

## 3. Do the sources agree on what is new? (by DOI)

| | W0 | W1 |
|---|---|---|
| PubMed edat / Europe PMC first index / Crossref created / OpenAlex publication | 43 / 55 / 59 / 30 | 55 / 51 / 63 / 55 |
| **Distinct papers in the union** | **111** (107 mention migraine) | 113 |
| Found by all four | 9 | 17 |
| Share of the union found by PubMed / Europe PMC / Crossref / OpenAlex | 39% / 50% / 53% / 27% | 49% / 45% / 56% / 49% |
| Found by exactly one source | Crossref 37, Europe PMC 12, OpenAlex 11, PubMed 0 | Crossref 22, Europe PMC 1, OpenAlex 18, PubMed 2 |

* **No single source gives the full picture in a window.** Even PubMed plus Europe PMC alone would miss the Crossref-only papers.
* **Crossref-only papers are real and mostly outside PubMed:** of 37 in W0, **34 are not in PubMed at all** and 3 are older PubMed records; 33 are journal articles. They include headache-specific journals (*Headache and Pain Research* 5, *Headache Medicine* 3, *Confinia Cephalalgica* 2) alongside low-profile titles (for example *National Journal of Medical Research & Yoga Sciences*, *International Journal of Homoeopathic Sciences*), so a source-quality screen is needed.
* **Europe PMC-only papers are old PubMed records** that Europe PMC indexed for the first time in the window (11 of 12 already in PubMed with an earlier entry date). They are removed by DOI dedupe.
* **No late arrivals seen in PubMed:** none of the 72 lookups (49 in W0, 23 in W1, overlapping) showed an entry date after the window. This cannot measure back-filling that happens after today; see section 7.
* The OpenAlex relevance count uses title only, so its "relevant" share is understated.

## 4. Rolling-window mechanics `[LIVE]` (PubMed `edat` runs; other sources by field)

* Runs of D-2, D-1 and D: 66, 55 and 43 hits. **New on each run: 66, 5, 2.** Three runs sum to 164 hits but only 73 distinct papers, so the overlap is mostly re-found papers, as intended.
* **Skipping the D-1 run loses nothing** in any of the four sources (0 papers lost).
* From the window definition (derived, not tested): a paper dated d is covered by the runs on days d to d+3, so it is lost only if all four are missed, assuming the source shows it by d+3.
* **Daily entry counts are uneven.** PubMed `edat` for 23 to 30 Oct: 16, 3, 16, 14, 19, 17, **5, 2**. The last two days look thin against 14 to 19 on the weekdays before. Either the days were quiet or they are still filling. Europe PMC: 12, 12, 21, 4, 14, 20, 13, 8. Crossref created: 12, 2, 2, 18, 13, 14, 18, 14. Crossref dips on the weekend (24 and 25 Oct); PubMed shows a Saturday dip and a Sunday catch-up (the 16 records behind the edge effect in section 2).

## 5. Category filters inside the window

**Gate wording first.** `migraine[tiab]` skips plurals and variants: over WIDE it finds 180 papers against 184 with `migraine*[tiab]` and 185 with `migrain*[tiab]` (the extras are "migraines", "migrainous"). Europe PMC: 275 against 280 with `TITLE_ABS:migrain*`. OpenAlex's search field is stemmed, so plurals are covered, and it rejects wildcards on that field (HTTP 400). Use `migrain*` for PubMed and Europe PMC. The table below was run with the plain `migraine` gate.

Cells are **W0 / WIDE** hits. "Gated" means the category terms AND migraine.

| # | Category | PubMed gated | PubMed ungated | Europe PMC gated | OpenAlex gated | Ungated W0 papers that mention migraine |
|---|---|---|---|---|---|---|
| 1 | A Migraine phenotype | 43 / 180 | 43 / 180 | 55 / 275 | 28 / 226 | 43 of 43 |
| 2 | A Trigeminovascular & neuropeptides | 5 / 32 | 15 / 70 | 6 / 34 | 3 / 39 | 5 of 15 |
| 3 | A Cortical excitability | 5 / 24 | 9 / 46 | 8 / 32 | 2 / 35 | 5 of 9 |
| 4 | A Clinical therapeutics | 6 / 38 | 1052 / 4778 | 7 / 42 | 2 / 37 | 0 of 150 |
| 5 | A Epidemiology & comorbidities | 8 / 51 | 728 / 3174 | 12 / 58 | 6 / 65 | 1 of 150 |
| 6 | B Mitochondria & bioenergetics | 0 / 0 | 372 / 1357 | 0 / 1 | 0 / 0 | 0 of 150 |
| 7 | B Redox & inflammation | 0 / 10 | 1260 / 4800 | 0 / 12 | 1 / 13 | 0 of 150 |
| 8 | B Nutrients & botanicals | 0 / 1 | 75 / 326 | 0 / 2 | 0 / 1 | 0 of 75 |
| 9 | B Hormonal regulation | 1 / 4 | 294 / 1315 | 1 / 7 | 0 / 4 | 0 of 150 |
| 10 | B Microbiome & gut-brain | 0 / 3 | 638 / 2387 | 0 / 4 | 0 / 3 | 0 of 150 |
| 11 | B Metabolism & vascular | 1 / 5 | 677 / 2678 | 1 / 4 | 1 / 10 | 0 of 150 |
| 12 | B Genetics & channels | 1 / 2 | 98 / 378 | 1 / 3 | 1 / 6 | 1 of 98 |

* **Without the migraine gate the Track B categories flood the window:** 372 to 1,260 PubMed papers in four days for categories 6, 7, 10 and 11, and nearly none mention migraine (0 of 150 checked in most). Gated, Track B returns 0 or 1 per window (0 to 10 over three weeks). The bridge list (decision 7) is what must widen this.
* **Ambiguous terms:** `CSD` alone gives 22 papers over WIDE, only 3 mention spreading depression or depolarization; with the migraine gate it gives 2, both about spreading depression. `ATP` gives 623 papers (0 of 150 checked mention migraine) and `NAD` 157 (0 of 150).
* **Match reason:** across the gated W0 hits, 66 of 70 contain a category term in the title or abstract. The other 4 (categories 2 to 4) matched PubMed's query without a literal term in the fields I pulled, possibly through author keywords (not verified). Store the query and do not assume the match can be rebuilt from the abstract (rule 4).
* **Cross-source:** gated counts are similar across sources; Europe PMC is higher (it includes PMC-only records and preprints) and OpenAlex lower in W0 (publication-date semantics). One unexplained gap: OpenAlex returns 39 ungated papers for category 3 in W0 against 9 in PubMed (not investigated).

### Language and article type

| Filter | Result |
|---|---|
| PubMed `English[la]` | 184 of 185 over WIDE (drops 1 Russian paper that has an abstract); W0 43 of 43 |
| Europe PMC `LANG:eng` | 265 of 280 over WIDE: **drops 15 papers**, and the Europe PMC record set has 14 records with no language value. Silent loss of about 5% |
| OpenAlex `language:en` | 254 of 257 (drops 3) |
| Excluding Comment, Editorial, Letter, News, Published Erratum, Retraction Notice, Retracted Publication | W0: 43 to 39; WIDE: 185 to 173. Removed were Letters (9), Editorial (1), Erratum (2); only 1 of the 12 had an abstract |

* **PubMed type names (all time, migraine):** `Retracted Publication` 19, **`Retraction Notice` 8** (the name `Retraction of Publication` returns 0; the earlier note in `Discovery_API_check.md` section 2.11 used the wrong name), Published Erratum 211, Comment 1,429, Editorial 733, Letter 1,813, News 129, Preprint 79.
* The noise-type exclusion must **not** drop retracted papers: the owner wants them kept and flagged.

## 6. Journal and author paths

**Journals** (WIDE, migraine gate, hits in PubMed / Europe PMC / Crossref per-journal route):

| Journal | PubMed | Europe PMC | Crossref |
|---|---|---|---|
| Headache | 15 | 16 | 12 |
| Frontiers in Neurology | 10 | 14 | 7 |
| J Headache Pain | 7 | 9 | 0 on the print ISSN, **9 on the online ISSN** |
| Cephalalgia | 6 | 6 | 6 |
| Neurology | 5 | 5 | 5 |
| JAMA Neurology | 1 | 1 | 0 |
| Lancet Neurology, Brain Behav Immun, Redox Biology, Nutrients | 0 | 0 | 0 |

* The PubMed ISSN filter returned only records of the requested journal (all returned records matched the ISSN).
* **Query every ISSN of a journal.** Crossref's per-journal route gave 0 under one ISSN and 9 under the other for the same journal.
* Without the migraine gate the 10 journals publish **248 papers in W0 and 786 over WIDE**, of which only 18 and 44 mention migraine (about 6 to 7%). The gate is what keeps the journal path usable.

**Authors** (30 researchers, owner-confirmed IDs):
* Only **2 of 30 had a migraine paper in W0** and 10 of 30 over WIDE, so the author path rarely fires in a 4-day window.
* Over 365 days PubMed name queries returned 299 papers; **90% (268 of 298 checked) also appear for the same author in OpenAlex**, and 68% (268 of 396) in the other direction. Lowest agreement: Buse 73%, Szperka 82%, Wang 83%, Lipton 85%.
* **Four researchers returned 0 from PubMed in 365 days** (Oshinsky, Tolner, van den Maagdenberg, Brennan). OpenAlex found 7 for van den Maagdenberg and 1 for Brennan, so PubMed name queries miss some people (not investigated). Use IDs where available.
* **Ungated name queries are noisy:** without the migraine gate the author path returned 5 papers in W0 (2 on migraine) and 27 over WIDE (10 on migraine); the rest look like other people with the same name (for example a burns study in Malawi).

**Three paths together (PubMed, migraine-gated):** W0: journal 18, author 2, topic 43, union **43**; WIDE: 44, 10, 185, union **185**. With the migraine gate the journal and author paths add nothing beyond the topic path in PubMed. Their value lies elsewhere: Crossref and Europe PMC finds, and any ungated Track B mode.

## 7. What this means for the design

1. **Search by entry or index date, never publication date:** PubMed `edat`, Europe PMC `FIRST_IDATE`, Crossref `created`, ClinicalTrials.gov `StudyFirstPostDate`. OpenAlex only offers publication date on the free key, so it supplements with a wider lookback and DOI dedupe.
2. **Union several sources.** In W0 any one source found at most 53% of the distinct papers. Crossref adds real papers PubMed never indexes, with a quality screen.
3. **The 3-day window works** for every index-date filter; one skipped daily run loses nothing.
4. **Gate with `migrain*`,** not `migraine`; keep the migraine gate for the journal and author paths.
5. **Apply the English rule after retrieval** (use the record's language, treat a missing value as unknown) because Europe PMC's `LANG:eng` silently drops empty-language records.
6. **Flag retracted papers before excluding noise types.**
7. **Guard against silent zeros** (ClinicalTrials.gov showed one): alert when a filter that normally returns results returns none.
8. **Query every ISSN** for a journal on Crossref.
9. **Late arrivals are the one thing this test could not measure.** A snapshot of the daily IDs from PubMed, Europe PMC and Crossref for 23 to 30 Oct was saved (`snapshot_2026-10-30.json`, scratchpad). Re-running the same queries tomorrow and counting records dated on or before today that were not in the snapshot gives the back-fill rate.

## 8. Not covered

Publisher RSS freshness, Unpaywall and the Springer API (no date filters), time-of-day effects, and the PubMed `edat` edge effect on other weekends. All numbers are from one day.

## 9. Late-arrival check, next-day re-run (2026-10-31)

**Question:** do records appear in a source *after* we looked, with an entry date on a day that was already over? If they do, a daily run over the last 3 days would not see them and a weekly safety net would be needed.

**Method** `[LIVE]`: on 2026-10-30 I saved, for each of the last 8 entry-date days (23 to 30 October), the record IDs that PubMed (`edat`), Europe PMC (`FIRST_IDATE`) and Crossref (created date) returned. On 2026-10-31 I repeated the same queries with the same wording (`migraine`, not the wider `migrain*` used in the build) and compared the sets day by day. OpenAlex was not included because its free plan has no entry-date filter.

| Source | Records in the snapshot | Late arrivals | Dropped from their day |
|---|---|---|---|
| PubMed | 92 | 3 (all on 30 October) | 0 |
| Europe PMC | 104 | 0 | 1 (24 October) |
| Crossref | 93 | 2 (all on 30 October) | 0 |

**Reading.** Every late arrival has the entry date of the day the snapshot was taken, and the snapshot was taken part-way through that day, so these are the day's later records, not back-dated ones. For the seven earlier days (23 to 29 October) no source gained a single record: 0 of 289 across the three sources. One Europe PMC record disappeared from its day (re-indexed or removed); it does not affect what we collect.

**Limits.** One night, three sources, about 100 records each. It shows no back-dating in that night; it cannot show the rate over a month. A back-dated record is one the daily 4-day window would miss on every later run, so the risk is real but, on this evidence, small.

