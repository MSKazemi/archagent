# TODOS — ArchAgent / BuildingOS

Deferred work captured during /plan-eng-review on 2026-05-27.
Each item below was proposed and confirmed via AskUserQuestion.

---

## TODO-1: Extracted text caching layer

**What:** Store pdftotext-extracted text in a `pdf_extractions` table keyed by SHA-256 of the uploaded PDF. Re-running analysis on the same document skips re-extraction.

**Why:** During concierge Phase 1, the founder will iterate on the LLM extraction prompt 5-10 times per tender. Without caching, each iteration re-runs pdftotext (30-60s). With caching, each iteration is just an LLM call.

**How to apply:** Add `pdf_extractions` table: `(id, pdf_sha256, notice_id, extracted_text, page_count, scanned_page_count, extraction_date)`. In `analyze_tender()`, check `SELECT extracted_text FROM pdf_extractions WHERE pdf_sha256=?` before running pdftotext.

**Depends on / blocked by:** D8 (PDF upload). Must decide whether to key by notice_id or PDF hash — SHA-256 is better (same notice may have amended documents).

---

## TODO-2: Async background analysis job with status polling

**What:** Replace synchronous `POST /api/dossier/analyze` with async job. POST returns `{job_id}` immediately. Client polls `GET /api/dossier/analyze/{job_id}` for status/result.

**Why:** Phase 2 self-serve clients cannot debug `curl --max-time 300`. Standard browser forms close the connection. Async is the correct design for a workflow that takes 60-120 seconds.

**How to apply:** SQLite `analysis_jobs` table: `(id, status, notice_id, result_json, error, created_at, completed_at)`. Background thread started by POST. Status endpoint polled by app.html at 5s intervals.

**Depends on / blocked by:** Phase 2 self-serve launch (Month 6+). The 150-page cap (D10) keeps Phase 1 within 300s synchronous budget.
