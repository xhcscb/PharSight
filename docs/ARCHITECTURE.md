# Pharsight Architecture and Evidence Standards

## Status and constraints

This document describes the intended architecture. The bootstrap does not add pharmaceutical runtime APIs, tools, prompts, schemas, or report behavior.

Pharsight is a full-history GPL-3.0 derivative of FinSight pinned initially to commit `8fc5bce28ac201c713219a47d30367c6a84342f4`. The sibling `../FinSight` checkout remains an immutable comparison point. All implementation, configuration, tests, outputs, and generated artifacts must stay within the Pharsight repository or its ignored runtime directories.

Python 3.11 is the supported project runtime. Python 3.12 is not considered supported until the complete test and report pipeline is verified on it.

## Architectural foundation

Retain FinSight's memory-centric sequence as the default product spine:

```text
Research request and mode
          |
          v
Data Collector ----> Deep Search / structured tools
          |
          v
Shared variable memory and checkpoints
          |
          v
Data Analyzer -----> deterministic Python analysis and VLM chart review
          |
          v
Report Generator --> outline, sections, citation/reference processing, DOCX/PDF
```

Pharmaceutical specialization should be introduced through explicit tools, mode-specific configuration and prompts, domain calculations, report outlines, and evaluation fixtures. Do not create a parallel orchestration stack unless a documented limitation of the upstream pipeline makes that necessary.

## Research modes

### Pharmaceutical industry research

The industry mode covers a declared market and subsector boundary, policy and reimbursement environment, epidemiology where investment-relevant, addressable-market assumptions, industry chain, technology/modalities, competitive landscape, supply and demand, catalysts, and risks.

It must not silently mix incompatible definitions such as prescription sales, terminal sales, ex-factory sales, procurement value, patient counts, or treated-patient counts. Market size and growth values require geography, period, currency, price basis, and calculation notes.

### Listed pharmaceutical company research

The company mode covers issuer disclosures and financial quality, product portfolio, pipeline assets and indications, trial and regulatory events, commercialization, addressable markets, peers, valuation assumptions, catalysts, and risks.

A/H identifiers and disclosure regimes must be explicit. Company claims must distinguish management guidance from audited results, regulator records, trial-registry records, analyst calculations, and model judgments. Pipeline status must be represented per asset, indication, jurisdiction, and trial rather than as one unqualified company-level status.

## Planned extension points

Future implementation should extend the existing repository in these areas:

```text
src/tools/pharma/                 # Regulatory, trial, literature, payment and disclosure tools
src/agents/*/prompts/             # Mode-specific collection, analysis and writing instructions
src/memory/prompts/               # Domain task generation and evidence selection
src/template/                     # Chinese industry and company outlines/templates
configs/                          # Sanitized example configurations for both research modes
tests/tools/pharma/               # Mocked contracts, parsing, provenance and failure cases
tests/fixtures/pharma/            # Small redistributable, versioned source fixtures
evaluations/                      # Ignored run artifacts; only approved summaries enter Git
```

Directories should be created only when implementing their first real, tested capability. Do not add empty modules or pretend implementations merely to match this plan.

New structured tools must continue to implement FinSight's `Tool`/`ToolResult` contract and participate in existing registration and memory flows. Any future extension to that public contract requires a decision record, backward-compatibility analysis, migration plan, and regression tests.

## Evidence-source policy

### Priority order

Use sources in this order unless a documented exception applies:

1. regulators, government statistics, exchanges, and official trial registries;
2. issuer filings and official company publications;
3. peer-reviewed literature and recognized biomedical databases;
4. reputable licensed financial or healthcare data vendors;
5. secondary research and general web sources.

The first implementation wave should assess these primary sources:

| Domain | Primary source | Intended evidence |
| --- | --- | --- |
| China approvals and product records | [NMPA query services](https://www.nmpa.gov.cn/zwfwqjd/index.html) | Approved drugs, imported drugs, manufacturers, device records where in scope |
| China drug review and trials | [CDE drug trial registry](https://www.chinadrugtrials.org.cn/) | Trial registration, sponsor, indication, phase/status, study details |
| China reimbursement and procurement | [National Healthcare Security Administration procurement](https://www.nhsa.gov.cn/col/col187/index.html) | National and linked procurement policies, bid documents, selected products and implementation notices |
| International trials | [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/about-api) | Structured study records, sponsors, interventions, outcomes, status and update times |
| Biomedical literature | [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/home/develop/api/) | PubMed/PMC identifiers, citations, abstracts and permitted full-text links |
| United States drug regulation | [openFDA drug APIs](https://open.fda.gov/apis/drug/) | Labels, adverse-event reports, recalls, Orange Book and Drugs@FDA-linked records |
| Hong Kong issuer disclosures | [HKEXnews](https://www3.hkexnews.hk/) | Results, annual/interim reports, announcements, prospectuses and other issuer filings |

A-share company research should prefer the disclosure portals designated by the relevant exchange or regulator and preserve the original filing URL and publication timestamp. Before automating any source, review its terms, robots/automation expectations, rate limits, update cadence, identifier semantics, and redistribution rights.

### Minimum evidence lineage

Every material collected record or report claim should be traceable to:

- source publisher and URL or stable record identifier;
- source publication/update date and retrieval timestamp;
- research cutoff timestamp;
- applicable company/security, drug/device, indication, geography, and reporting period;
- original unit/currency and any normalized unit/currency;
- transformation or formula notes;
- evidence class: sourced fact, deterministic calculation, explicit assumption, or model judgment.

This is a behavioral requirement, not a runtime schema introduced by the bootstrap. The concrete representation will be specified in M2 and must remain compatible with existing `ToolResult` consumers or include an explicit migration.

### Conflicts, missingness, and freshness

- Preserve missing values as missing; never replace them with model-generated estimates without an explicit assumption label.
- When sources conflict, retain both records, apply the source-priority rule, and record the resolution or unresolved conflict.
- Track both event date and source update/publication date. They are not interchangeable.
- Flag stale trial, approval, procurement, and financial records against source-specific freshness rules.
- Freeze a report cutoff time so a reproduced run can explain why later information was excluded.

## Analysis and report controls

- Domain calculations must be deterministic functions with formula documentation, controlled inputs, units, and fixture-based tests.
- Valuation outputs must expose forecast and discounting assumptions and must not imply precision beyond the evidence.
- Trial probability, market share, pricing, patient population, and launch timing assumptions must be clearly labeled and sensitivity-tested when material.
- Charts and tables must carry source, period, unit, and cutoff annotations; report prose must retain citation linkage to their underlying evidence.
- Reports must include investment-risk disclosures and a statement that the content is not medical advice.
- A failed, blocked, rate-limited, or incomplete source must be visible in the run record and report limitations; it must not be silently replaced with invented data.

## Testing strategy

1. **Contract tests:** tool metadata, parameter validation, `ToolResult` shape, provenance, and serialization.
2. **Parser tests:** frozen, legally redistributable fixtures for normal, empty, malformed, renamed-field, and partial records.
3. **Calculation tests:** formulas, units, currencies, date boundaries, missingness, and sensitivity cases.
4. **Agent regression tests:** controlled memory inputs verify mode routing, evidence labels, citations, and report-outline behavior.
5. **Opt-in integration tests:** live source checks with timeouts, retries, rate limits, no credentials in logs, and no brittle value assertions.
6. **End-to-end evaluation:** frozen A-share, H-share, and industry cases scored for factuality, citation coverage, reproducibility, and report quality.

No live network dependency should be required for the default unit-test suite.

## Architecture decisions

| ID | Date | Decision | Consequence |
| --- | --- | --- | --- |
| ADR-001 | 2026-09-14 | Maintain Pharsight as an independent full-history fork rather than modifying the sibling checkout | Domain code can evolve independently; GPL obligations and upstream synchronization must remain explicit |
| ADR-002 | 2026-09-14 | Support both A/H pharmaceutical industry and listed-company research | Prompts, configurations, outlines, fixtures, and evaluation must keep the two modes distinct |
| ADR-003 | 2026-09-14 | Target a university investment-research audience with securities-report conventions | Reproducibility and method disclosure are release gates alongside report presentation |
| ADR-004 | 2026-09-14 | Standardize on Python 3.11 | Development and CI must verify 3.11; other versions require separate compatibility evidence |
| ADR-005 | 2026-09-14 | Preserve the existing FinSight orchestration as the initial product spine | Prefer domain extensions and tested adapters before considering orchestration replacement |

Future entries should record the problem, considered alternatives, decision, consequences, owner, and superseding decision when applicable.
