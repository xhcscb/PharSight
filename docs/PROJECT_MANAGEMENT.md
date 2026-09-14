# Pharsight Project Management

## Project charter

**Mission:** build a reproducible pharmaceutical investment-research agent on the FinSight multi-agent foundation for a university research and demonstration project.

**Primary users:** student researchers and reviewers who need securities-research-style outputs with transparent methods, inspectable evidence, and reproducible calculations.

**Product modes:**

1. **A/H pharmaceutical industry research** — subsector definition, policy and payment environment, market sizing, industry chain, competitive landscape, technology trends, catalysts, and risks.
2. **A/H listed-company research** — financial quality, product and pipeline portfolio, clinical and regulatory progress, commercialization, competitive positioning, valuation assumptions, catalysts, and risks.

Global clinical, regulatory, and biomedical-literature evidence may supplement either mode. Chinese output is the default. Pharsight provides investment-research assistance only and does not provide medical advice.

## Current baseline

| Item | Decision |
| --- | --- |
| Status | M0 project bootstrap |
| Upstream | `RUC-NLPIR/FinSight` |
| Pinned baseline | `8fc5bce28ac201c713219a47d30367c6a84342f4` |
| License | GPL-3.0; preserve the upstream license, notices, and source obligations |
| Standard runtime | Python 3.11 |
| Repository model | Independent full-history fork with official repository configured as `upstream` |
| Team remote | Not configured; add `origin` only after the project owner selects a remote |

The sibling `../FinSight` directory is a read-only reference and must never receive Pharsight changes or generated files.

## Work organization

### Roles

One person may hold multiple roles, but every tracked item must have one accountable owner.

| Role | Responsibility |
| --- | --- |
| Project lead | Scope, priorities, milestone acceptance, external coordination |
| Domain research | Pharmaceutical taxonomy, research questions, source interpretation, report review |
| Data and tooling | Connectors, normalization, provenance, rate limits, data-quality tests |
| Agent and report | Tasks, prompts, analysis workflow, charts, templates, report assembly |
| Quality and evaluation | Test design, benchmark cases, citation audit, reproducibility, release evidence |

### Task lifecycle

Use the following single status field:

`Backlog -> Ready -> In progress -> Review -> Done`

Use `Blocked` only when the blocking condition, owner, dependency, and next action are recorded. Move the item back to its prior active state when the blocker clears.

Each task record must contain:

- stable ID and concise title;
- research mode and user outcome;
- owner, status, priority, and milestone;
- dependencies and data-access constraints;
- acceptance criteria and required tests;
- evidence sources and licensing notes when data is involved;
- risks, decisions, and follow-up work discovered during implementation.

### Branches, commits, and review

- Keep `main` releasable and use `feature/<ticket>-<slug>`, `fix/<ticket>-<slug>`, or `docs/<ticket>-<slug>` for normal work.
- Rebase or merge the latest local `main` before review; never force-push shared branches without team agreement.
- Keep one logical concern per branch and commit. Use imperative Conventional Commit subjects.
- Review must confirm scope, evidence lineage, date/unit semantics, failure handling, tests, documentation, and absence of secrets or restricted data.
- Synchronize from `upstream` through an explicit maintenance task with regression tests and a recorded compatibility note.

## Roadmap

| Milestone | Goal | Exit criteria | Status |
| --- | --- | --- | --- |
| M0 — Bootstrap | Establish the independent repository, boundaries, governance, and architecture | Full history retained; upstream pinned; management documents linked; source repository unchanged | Done |
| M1 — Reproducible baseline | Reproduce upstream behavior in a clean Python 3.11 environment | Documented setup; dependency installation succeeds; upstream tests and one non-secret smoke path have recorded results | Backlog |
| M2 — Pharmaceutical evidence tools | Add reliable structured access to regulatory, trial, literature, payment/procurement, and A/H disclosure sources | Connectors follow `ToolResult`; provenance retained; mocked unit tests and opt-in live tests cover normal, empty, malformed, rate-limit, and timeout responses | Backlog |
| M3 — Dual-mode research workflow | Add separate industry and listed-company configurations, tasks, prompts, outlines, and report templates | Both modes run end to end against controlled fixtures; outputs clearly distinguish sourced facts, calculations, assumptions, and judgments | Backlog |
| M4 — Domain analytics | Add pipeline, competitive-landscape, financial/valuation, catalyst, and risk modules | Deterministic calculations have fixtures and formulas; domain reviewers approve representative outputs | Backlog |
| M5 — Evaluation and demonstration | Validate factual accuracy, citation completeness, reproducibility, and presentation | Benchmark suite passes agreed thresholds; A-share, H-share, and industry demonstrations are reproducible and documented | Backlog |

## Initial backlog

| ID | Work item | Milestone | Priority | Status |
| --- | --- | --- | --- | --- |
| M1-001 | Define and verify the Python 3.11 development environment and dependency lock strategy | M1 | P0 | Backlog |
| M1-002 | Run and record the unchanged upstream unit-test baseline | M1 | P0 | Backlog |
| M1-003 | Define a secret-free smoke test and output-retention policy | M1 | P1 | Backlog |
| M2-001 | Specify the common evidence metadata and source-quality rules | M2 | P0 | Backlog |
| M2-002 | Prototype NMPA/CDE approval and China trial-registry access | M2 | P0 | Backlog |
| M2-003 | Prototype ClinicalTrials.gov and PubMed/NCBI access | M2 | P0 | Backlog |
| M2-004 | Assess national reimbursement/procurement and A/H disclosure access, terms, and rate limits | M2 | P0 | Backlog |
| M2-005 | Add connector contract, fixture, and provenance tests | M2 | P0 | Backlog |
| M3-001 | Define pharmaceutical industry research tasks and Chinese report outline | M3 | P0 | Backlog |
| M3-002 | Define listed-company research tasks and Chinese report outline | M3 | P0 | Backlog |
| M3-003 | Add mode-specific prompts, configuration validation, and controlled end-to-end fixtures | M3 | P0 | Backlog |
| M4-001 | Specify pipeline asset/stage normalization and event timelines | M4 | P0 | Backlog |
| M4-002 | Specify market sizing, peer comparison, valuation assumptions, catalysts, and risk calculations | M4 | P1 | Backlog |
| M5-001 | Select representative A-share, H-share, and industry benchmark cases with frozen cutoffs | M5 | P0 | Backlog |
| M5-002 | Define factuality, citation, calculation, reproducibility, and report-quality scorecards | M5 | P0 | Backlog |

Priority meanings: P0 blocks the milestone outcome; P1 is important but not blocking; P2 is an improvement that may be deferred.

## Quality gates

### Definition of Ready

A task may enter `Ready` only when its user outcome, boundaries, source permissions, dependencies, acceptance criteria, and test approach are unambiguous.

### Definition of Done

A task may enter `Done` only when:

- acceptance criteria and relevant tests pass;
- provenance, cutoff dates, units, currencies, and calculations are auditable;
- errors and unavailable data fail visibly rather than producing invented results;
- documentation and project status are current;
- no secrets, restricted data, caches, or generated reports are staged;
- the sibling upstream repository remains clean and unchanged;
- residual risks and deferred work are recorded.

### Release readiness

A milestone release requires a clean checkout reproduction, a recorded Python/dependency environment, passing automated gates, a citation audit, domain review, known-limitations notes, and demonstration inputs whose information cutoffs are fixed.

## Risk register

| Risk | Impact | Mitigation | Review point |
| --- | --- | --- | --- |
| Regulatory sites lack stable public APIs or change anti-automation controls | Missing or brittle China evidence | Prefer documented endpoints/downloads, respect terms and rate limits, retain manual-ingest fallback, fail visibly | M2 design and each source change |
| Drug, indication, company, sponsor, and ticker names do not align across sources | Incorrect joins and duplicated pipeline assets | Introduce reviewed identifiers and alias mappings; retain original labels and source IDs | M2/M4 |
| Clinical status or approval dates become stale | Misleading catalyst and valuation analysis | Store retrieval and source-update times, enforce report cutoffs, flag stale records | Every run and evaluation |
| Secondary sources conflict with primary records | Incorrect factual conclusions | Apply source priority, expose conflicts, and require reviewer resolution for material facts | Collection and report review |
| Model output overstates clinical or investment certainty | Unsafe or misleading report | Label assumptions/judgments, require uncertainty and risk disclosures, add factual/citation evaluation | M3 onward |
| Upstream changes break the fork | Regression or difficult maintenance | Pin the base, isolate domain changes, synchronize through reviewed maintenance tasks | Before each upstream sync |
| Data licensing prevents redistribution | Compliance and reproducibility gaps | Record terms before integration; keep restricted raw data out of Git; prefer redistributable fixtures | Source approval and release |

## Project reporting cadence

- Update task status whenever ownership, scope, blocker, or acceptance state changes.
- At each milestone review, record completed outcomes, test evidence, unresolved risks, and the next milestone decision.
- Update architecture decisions in the same change that introduces or reverses them.
- Treat generated reports as evaluation artifacts, not as source code; retain only approved, sanitized examples.
