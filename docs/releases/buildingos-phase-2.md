# Release — BuildingOS Phase 2: Italian Procurement Intelligence

**Date:** 2026-05-29
**Scope:** 10 new features + 5 improvements. Pure Python stdlib, zero new dependencies.

## Summary

A new evidence-based domain module, `archagent/intelligence/procurement.py`, encodes the operational
rules of Italian public-works procurement under **D.Lgs. 36/2023** (Codice dei Contratti Pubblici).
Every numeric constant is sourced to a legal article and to the in-repo research sessions
(`.claude/plans/research/`). The module is deterministic and side-effect free (no I/O, no LLM, no
network), and is wired into compliance scoring, proposal/compliance-matrix generation, dossiers, and
two new API endpoints.

This release directly targets the credibility gaps identified in an internal simulated-expert design review: the
old compliance matrix produced 15 generic identical rows, and buyer-facing proposals leaked
ArchAgent's internal pricing.

## New features

| # | Feature | Entry point |
|---|---------|-------------|
| 1 | SOA category catalog (OG1–13, OS1–35) | `GET /api/soa-categories`, `soa_category_catalog()` |
| 2 | CIG extraction & validation (10-char alnum, >€40K) | `extract_cig`, `is_valid_cig` |
| 3 | SOA-class inference from contract value | `infer_soa_class` |
| 4 | Cauzione provvisoria (2%, ISO reductions, −80% cap) | `cauzione_provvisoria` |
| 5 | Cauzione definitiva (10% + ribasso surcharge, 30% cap) | `cauzione_definitiva` |
| 6 | Document-waterfall generator (DGUE→DURC→PASSOE→SOA→cauzioni→antimafia→CCIAA→bilanci) | `required_documents` |
| 7 | D.Lgs 36/2023 readiness checklist | `dlgs36_checklist` |
| 8 | PNRR / NextGenerationEU tagger (+ mission detection) | `detect_pnrr` |
| 9 | Deadline / urgency tracker | `deadline_status` |
| 10 | Go / No-Go bid decision scorer | `go_no_go` |
| 11 | One-shot text analysis endpoint | `POST /api/procurement/analyze-text` |

## Improvements

1. **`compliance.py`** — returns Phase-2 intelligence (`cig`, `inferred_soa_class`,
   `cauzione_provvisoria`, `pnrr`, `deadline_status`, `go_no_go`, `document_waterfall`,
   `dlgs36_checklist`) **additively**; the legacy `gaps`/`matched_requirements`/`bid_readiness_score`
   contract is unchanged.
2. **`proposals.generate_compliance_matrix`** — Italy leads now lead with the real document waterfall,
   each row carrying a legal citation (e.g. *Art. 19 D.Lgs 36/2023*), CIG/SOA/PNRR detection, instead
   of generic "explicit assumption" rows.
3. **`proposals.generate_proposal`** — removed the ArchAgent commercial-offer/pricing block from the
   buyer-facing proposal.
4. **`dossiers.py`** — new "Procurement intelligence (D.Lgs 36/2023)" section: CIG, inferred SOA
   class, deadline urgency, cauzioni estimates, PNRR signals, document waterfall.
5. **`scoring.py`** — PNRR signal boost and broader Italian term coverage (anti-seismic,
   school/healthcare, heat-pump, etc.); now returns `pnrr` / `pnrr_signals`.

## Verification

- `tests/test_procurement.py` — 36 tests, all pass.
- `tests/test_analyst.py` (21), `tests/test_db.py` (7), `tests/test_italy.py`, `ops/maintenance.py` —
  green; compliance contract confirmed stable.
- Full `archagent/` package compiles; no new imports outside the standard library.

## Notes / follow-ups

- `test_smoke` / `test_regression` / `test_admin` spawn a live HTTP server — run them in a normal
  shell to confirm the two new routes end-to-end (some sandboxes block socket bind).
- Phase-2 endpoints are API-only; surfacing them in the app/admin UI is a Phase-3 item (see ROADMAP).
