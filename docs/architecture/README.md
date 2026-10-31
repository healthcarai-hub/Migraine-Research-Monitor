# Architecture

This folder explains **how the system is built and why**, written as a design study: the high-level design, the low-level design, the API, the data, and how it is kept reliable and secure. Each chapter starts with an **In short** box, so you can stop after the first paragraph or read on for the detail.

| # | Chapter | The question it answers |
|---|---|---|
| 1 | [High-level design](01-high-level-design.md) | What is it, what must it do, and how are the big pieces arranged? |
| 2 | [Low-level design](02-low-level-design.md) | How does each part work inside, and which algorithms matter? |
| 3 | [API design](03-api-design.md) | What does the API offer, and how are access, limits and errors handled? |
| 4 | [Data design](04-data-design.md) | What is stored, with which keys and constraints, and why? |
| 5 | [Reliability, security and operations](05-reliability-security-operations.md) | What breaks, what is the threat model, and how is it built, tested and run? |
| 6 | [Decision log](06-decision-log.md) | Why was it designed this way, and what was rejected? |
| 7 | [Data sources and ingestion](07-data-sources-and-ingestion.md) | Where does the data come from, how is it retrieved, what are the fallbacks, and what did we observe? |

## Reading paths

| If you want to... | Read |
|---|---|
| understand the whole system in five minutes | the README, then chapter 1 up to "How the data flows" |
| find out why a paper did or did not reach a newsletter | chapter 2: sections on the gate, selection and ranking; then the decision log, D5 and D9 |
| change the search | chapter 3, section 3.6; chapter 4, section 4.7 |
| understand where the data comes from and what each source is good for | chapter 7 |
| add a source or a full-text route | chapter 7 first, then chapter 2, sections 2.3, 2.4 and 2.18 |
| change the database | chapter 4, sections 4.5 and 4.12; decision D14 |
| be on call when a job fails | chapter 5, section 5.1 |
| review security | chapter 3, section 3.4; chapter 4, section 4.13; chapter 5, section 5.5 |
| ask "why is it like this?" | chapter 6 |

## Glossary

| Term | Meaning |
|---|---|
| **field** | a research area and what it means: the gate word, the topics, the mechanism links |
| **profile** | one client's watch list within a field: journals, researchers, newsletter settings |
| **gate** | the test that a paper mentions the field's word in its title or abstract |
| **source** | an outside index the system searches (PubMed, Europe PMC, Crossref, OpenAlex, ClinicalTrials.gov) |
| **search spec** | one concrete search the daily job runs |
| **hit** | one record of "this search found this paper" |
| **match** | what a paper means for a profile: topics, monitored journal or researcher |
| **monitored / watched** | a journal or researcher the profile asked to follow |
| **track** | direct evidence on the field, or an indirect mechanism |
| **edition** | one weekly newsletter, stored whole once built |
| **window** | the span of first-stored dates an edition draws from |
| **carried over** | a paper held back by an earlier edition that returns once, ranked lower |
| **switch** | an on/off setting that changes what the next newsletter picks |
| **draft** | an email saved in Gmail for a person to review; the program never sends |

Back to the [project README](../../README.md).
