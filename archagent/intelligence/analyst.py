#!/usr/bin/env python3
"""
analyst.py — BuildingOS Phase 1: LLM tender PDF analysis.

Extracts structured bid intelligence from Italian public tender PDFs
using Azure OpenAI. Handles digital (pdftotext) and scanned
(pdftoppm + vision) documents. 150-page cap. Sequential chunking.

Required env vars:
  AZURE_OPENAI_KEY        — Azure API key
  AZURE_OPENAI_ENDPOINT   — e.g. https://myresource.openai.azure.com
  AZURE_OPENAI_DEPLOYMENT — e.g. gpt-5.4  (must support vision + JSON mode)
  AZURE_OPENAI_API_VERSION — e.g. 2025-04-01-preview (Responses API)

Usage (standalone):
  python3 analyst.py <path-to-tender.pdf> [--notice-id NOTICE_ID]
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ─── Constants ────────────────────────────────────────────────────────────────

MAX_PAGES = 150          # page cap — eligibility info is always in first 100 pages
CHUNK_CHARS = 150_000    # ~40K tokens; leaves room for system prompt + prior JSON
MIN_CHARS_PER_PAGE = 80  # below this average → document is mostly scanned
MAX_SCANNED_PAGES = 10   # vision pages per call; gpt-4o supports multi-image
LLM_TIMEOUT_S = 120      # seconds per API call
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB hard cap

# Cost estimate: gpt-4o input $2.50/1M tokens, output $10/1M tokens.
# 1 token ≈ 3.5 chars in Italian bureaucratic text.
_COST_PER_1K_INPUT_EUR = 0.0023   # $2.50/1M ≈ €0.0023/1K
_COST_PER_1K_OUTPUT_EUR = 0.0092  # $10/1M ≈ €0.0092/1K

# ─── Extraction prompt ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Sei un analista di gare d'appalto pubbliche italiane. Estrai le informazioni
richieste dal testo del bando/disciplinare. Rispondi SOLO con JSON valido, nessun testo aggiuntivo.

Schema di output:
{
  "buyer": "nome stazione appaltante",
  "tender_title": "oggetto del contratto",
  "deadline": "ISO 8601 o null",
  "value_eur": numero o null,
  "cpv_codes": ["lista codici CPV"],
  "soa_required": [{"category": "OGxx o OSxx", "classification": "cifra romana I-VIII"}],
  "certifications_required": ["es. ISO 9001:2015", "SOA OG11-IV"],
  "evaluation_criteria": {"technical": percentuale, "price": percentuale},
  "submission_format": "telematica/cartacea/mista",
  "lot_count": numero intero,
  "geographic_scope": "regione italiana o null",
  "warnings": ["informazioni ambigue o contraddittorie trovate"]
}

Regole:
- soa_required: includi TUTTE le categorie SOA e classificazioni trovate (es. OG1, OG11, OS28)
- Classificazioni SOA: usa cifre romane I, II, III, III-bis, IV, IV-bis, V, VI, VII, VIII
- Se un campo non è trovato usa null o array vuoto []
- Se vengono fornite estrazioni precedenti (prior_extraction), AGGIORNA il JSON con nuove informazioni senza perdere dati già estratti
- warnings[] solo per ambiguità genuine o conflitti nei dati
"""

# ─── Azure OpenAI client ──────────────────────────────────────────────────────

def _azure_endpoint() -> tuple[str, str, str]:
    """Return (url, api_key, deployment). Raises EnvironmentError if not configured."""
    key = os.environ.get('AZURE_OPENAI_KEY', '').strip()
    endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT', '').strip().rstrip('/')
    deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-5.4').strip()
    version = os.environ.get('AZURE_OPENAI_API_VERSION', '2025-04-01-preview').strip()
    if not key or not endpoint:
        raise EnvironmentError(
            'AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT must be set in .env'
        )
    url = f'{endpoint}/openai/responses?api-version={version}'
    return url, key, deployment


def _to_responses_input(messages: list[dict]) -> list[dict]:
    """
    Convert chat-style messages to Responses API input format.

    System, user, and assistant roles are kept as-is; content blocks are
    converted from Chat Completions types to Responses API types.
    The Responses API requires "json" to appear in the input (not instructions)
    when json_object mode is used, so system messages stay in the input array.
    """
    input_msgs: list[dict] = []

    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')

        if isinstance(content, str):
            blocks = [{'type': 'input_text', 'text': content}]
        else:
            blocks = []
            for part in content:
                if part.get('type') == 'text':
                    blocks.append({'type': 'input_text', 'text': part['text']})
                elif part.get('type') == 'image_url':
                    # Chat Completions: {"type":"image_url","image_url":{"url":"data:..."}}
                    # Responses API:    {"type":"input_image","image_url":"data:..."}
                    url = part['image_url']['url'] if isinstance(part.get('image_url'), dict) else part.get('image_url', '')
                    blocks.append({'type': 'input_image', 'image_url': url})
                elif part.get('type') in ('input_text', 'input_image'):
                    blocks.append(part)  # already in correct format

        input_msgs.append({'role': role, 'content': blocks})

    return input_msgs


def _call_azure(messages: list[dict], *, json_mode: bool = True) -> tuple[dict, int]:
    """POST to Azure OpenAI Responses API. Returns (parsed_response, total_tokens)."""
    url, key, deployment = _azure_endpoint()
    input_msgs = _to_responses_input(messages)

    body: dict[str, Any] = {
        'model': deployment,
        'input': input_msgs,
        'max_output_tokens': 4096,
        'temperature': 0,
    }
    if json_mode:
        body['text'] = {'format': {'type': 'json_object'}}

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json', 'api-key': key},
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:400]
        raise RuntimeError(f'Azure API HTTP {exc.code}: {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Azure API network error: {exc.reason}') from exc

    tokens = result.get('usage', {}).get('total_tokens', 0)
    return result, tokens


def _extract_json(response: dict) -> dict:
    """Pull JSON from an Azure Responses API response."""
    text = response['output'][0]['content'][0]['text']
    return json.loads(text)


# ─── PDF text extraction ──────────────────────────────────────────────────────

def _check_deps() -> dict[str, bool]:
    """Return availability of system binaries."""
    return {
        'pdftotext': bool(shutil.which('pdftotext')),
        'pdftoppm': bool(shutil.which('pdftoppm')),
    }


def _extract_text(pdf_bytes: bytes) -> tuple[str, int, bool]:
    """
    Extract text from PDF bytes using pdftotext (max MAX_PAGES pages).

    Returns:
        (text, pages_extracted, is_truncated)
    """
    deps = _check_deps()
    if not deps['pdftotext']:
        raise RuntimeError('pdftotext not found — install poppler-utils')

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(pdf_bytes)

    try:
        # -l MAX_PAGES caps page extraction; '-' writes to stdout
        result = subprocess.run(
            ['pdftotext', '-l', str(MAX_PAGES), tmp_path, '-'],
            capture_output=True,
            timeout=60,
        )
        text = result.stdout.decode('utf-8', errors='replace')

        # Count pages actually extracted via pdfinfo (best effort)
        info = subprocess.run(
            ['pdfinfo', tmp_path],
            capture_output=True,
            timeout=10,
        )
        total_pages = 0
        for line in info.stdout.decode(errors='replace').splitlines():
            if line.lower().startswith('pages:'):
                try:
                    total_pages = int(line.split(':', 1)[1].strip())
                except ValueError:
                    pass

        pages_extracted = min(total_pages, MAX_PAGES) if total_pages else MAX_PAGES
        is_truncated = total_pages > MAX_PAGES
        return text, pages_extracted, is_truncated
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _is_scanned(text: str, pages: int) -> bool:
    """Return True if the document appears to be a scanned image (little extractable text)."""
    if pages == 0:
        return True
    avg_chars = len(text.strip()) / max(pages, 1)
    return avg_chars < MIN_CHARS_PER_PAGE


def _extract_scanned_images(pdf_bytes: bytes, max_pages: int = MAX_SCANNED_PAGES) -> list[str]:
    """
    Convert PDF pages to PNG base64 strings using pdftoppm.
    Returns list of base64-encoded PNG strings (one per page, up to max_pages).
    """
    deps = _check_deps()
    if not deps['pdftoppm']:
        return []  # fallback: scanned doc without pdftoppm → analyzed as empty

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_pdf = Path(tmpdir) / 'input.pdf'
        tmp_pdf.write_bytes(pdf_bytes)
        prefix = Path(tmpdir) / 'page'

        subprocess.run(
            [
                'pdftoppm',
                '-r', '120',           # 120 DPI — readable but not huge files
                '-l', str(max_pages),  # first max_pages pages only
                '-png',
                str(tmp_pdf),
                str(prefix),
            ],
            capture_output=True,
            timeout=120,
        )

        images = []
        for png_file in sorted(Path(tmpdir).glob('page-*.png')):
            img_bytes = png_file.read_bytes()
            images.append(base64.b64encode(img_bytes).decode())

    return images


# ─── LLM analysis ─────────────────────────────────────────────────────────────

def _empty_extraction() -> dict:
    return {
        'buyer': None,
        'tender_title': None,
        'deadline': None,
        'value_eur': None,
        'cpv_codes': [],
        'soa_required': [],
        'certifications_required': [],
        'evaluation_criteria': {},
        'submission_format': None,
        'lot_count': 1,
        'geographic_scope': None,
        'warnings': [],
    }


def _chunk_text(text: str) -> list[str]:
    """Split text into chunks of at most CHUNK_CHARS characters at paragraph boundaries."""
    if len(text) <= CHUNK_CHARS:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_CHARS
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Try to break at a paragraph boundary (double newline)
        split = text.rfind('\n\n', start, end)
        if split == -1:
            split = text.rfind('\n', start, end)
        if split == -1:
            split = end
        chunks.append(text[start:split])
        start = split
    return [c for c in chunks if c.strip()]


def _analyze_text_chunks(chunks: list[str]) -> tuple[dict, int]:
    """
    Sequentially analyze text chunks, accumulating extraction context.
    Returns (extracted_json, total_tokens).
    """
    prior = _empty_extraction()
    total_tokens = 0
    chunk_count = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        user_content = f'Chunk {i}/{chunk_count} del documento:\n\n{chunk}'
        if i > 1:
            user_content += f'\n\nESTRAZIONE PRECEDENTE (aggiorna con nuovi dati):\n{json.dumps(prior, ensure_ascii=False)}'

        messages = [
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ]
        resp, tokens = _call_azure(messages, json_mode=True)
        total_tokens += tokens
        try:
            extracted = _extract_json(resp)
            # Merge: keep prior data if new extraction has None/empty for a field
            for key, val in extracted.items():
                if key == 'warnings':
                    prior['warnings'] = list(set((prior.get('warnings') or []) + (val or [])))
                elif key in ('soa_required', 'cpv_codes', 'certifications_required'):
                    merged = prior.get(key) or []
                    for item in (val or []):
                        if item not in merged:
                            merged.append(item)
                    prior[key] = merged
                elif val not in (None, '', [], {}):
                    prior[key] = val
        except (json.JSONDecodeError, KeyError):
            prior.setdefault('warnings', []).append(
                f'Chunk {i}: LLM returned malformed JSON — partial extraction'
            )

    return prior, total_tokens


def _analyze_scanned_images(images: list[str]) -> tuple[dict, int]:
    """
    Analyze scanned PDF pages via Azure vision.
    Sends up to MAX_SCANNED_PAGES images in one message.
    Returns (extracted_json, total_tokens).
    """
    if not images:
        return _empty_extraction(), 0

    content: list[dict] = [
        {
            'type': 'text',
            'text': (
                'Queste sono le prime pagine di un bando di gara italiano (immagini scansionate). '
                'Estrai tutte le informazioni richieste secondo lo schema JSON del sistema.\n\n'
                + _SYSTEM_PROMPT
            ),
        }
    ]
    for b64 in images[:MAX_SCANNED_PAGES]:
        content.append({
            'type': 'image_url',
            'image_url': {'url': f'data:image/png;base64,{b64}', 'detail': 'high'},
        })

    messages = [{'role': 'user', 'content': content}]
    resp, tokens = _call_azure(messages, json_mode=True)
    try:
        extracted = _extract_json(resp)
    except (json.JSONDecodeError, KeyError):
        extracted = _empty_extraction()
        extracted['warnings'].append('Vision extraction returned malformed JSON')

    return extracted, tokens


# ─── Cost calculation ─────────────────────────────────────────────────────────

def _cost_eur(total_tokens: int) -> float:
    # Rough split: 80% input, 20% output
    input_k = total_tokens * 0.8 / 1000
    output_k = total_tokens * 0.2 / 1000
    return round(input_k * _COST_PER_1K_INPUT_EUR + output_k * _COST_PER_1K_OUTPUT_EUR, 4)


# ─── Main entry point ─────────────────────────────────────────────────────────

def analyze_tender(
    pdf_bytes: bytes,
    notice_id: str = '',
    _cached_text: tuple | None = None,
    _on_extracted=None,
) -> dict:
    """
    Analyze a tender PDF and return structured extraction.

    Returns a typed result dict:
    {
      status: 'ok' | 'partial' | 'error',
      step: which step failed (if error),
      extracted_json: structured tender data,
      cost_estimate_eur: float,
      pages_analyzed: int,
      pages_total: int (0 if unknown),
      is_truncated: bool,
      is_scanned: bool,
      warnings: [str],
      error: str or None,
    }
    """
    if len(pdf_bytes) > MAX_PDF_BYTES:
        return {
            'status': 'error',
            'step': 'size_check',
            'extracted_json': None,
            'cost_estimate_eur': 0.0,
            'pages_analyzed': 0,
            'pages_total': 0,
            'is_truncated': False,
            'is_scanned': False,
            'warnings': [],
            'error': f'PDF exceeds {MAX_PDF_BYTES // (1024*1024)}MB size limit',
        }

    warnings: list[str] = []

    # Step 1: extract text (use cache hit if provided, otherwise run pdftotext)
    if _cached_text is not None:
        text, pages_extracted, is_truncated = _cached_text
        warnings.append('Text extraction served from cache (pdftotext skipped).')
    else:
        try:
            text, pages_extracted, is_truncated = _extract_text(pdf_bytes)
            if _on_extracted is not None:
                try:
                    _on_extracted(text, pages_extracted, is_truncated)
                except Exception:
                    pass  # cache write failure is non-fatal
        except Exception as exc:
            return {
                'status': 'error',
                'step': 'text_extraction',
                'extracted_json': None,
                'cost_estimate_eur': 0.0,
                'pages_analyzed': 0,
                'pages_total': 0,
                'is_truncated': False,
                'is_scanned': False,
                'warnings': [],
                'error': str(exc),
            }

    if is_truncated:
        warnings.append(
            f'Document truncated at page {MAX_PAGES}. Annexes beyond page {MAX_PAGES} not analyzed.'
        )

    scanned = _is_scanned(text, pages_extracted)

    # Step 2: LLM extraction
    try:
        if scanned:
            deps = _check_deps()
            if not deps['pdftoppm']:
                warnings.append(
                    'Scanned document detected but pdftoppm not found — text extraction may be incomplete.'
                )
                images = []
            else:
                images = _extract_scanned_images(pdf_bytes)

            if images:
                extracted, tokens = _analyze_scanned_images(images)
                warnings.append(
                    f'Scanned document: vision analysis on first {len(images)} pages only.'
                )
            else:
                # Fall back to whatever pdftotext managed to get
                chunks = _chunk_text(text) if text.strip() else ['']
                extracted, tokens = _analyze_text_chunks(chunks)
        else:
            chunks = _chunk_text(text)
            extracted, tokens = _analyze_text_chunks(chunks)
    except EnvironmentError as exc:
        return {
            'status': 'error',
            'step': 'llm_config',
            'extracted_json': None,
            'cost_estimate_eur': 0.0,
            'pages_analyzed': pages_extracted,
            'pages_total': 0,
            'is_truncated': is_truncated,
            'is_scanned': scanned,
            'warnings': warnings,
            'error': str(exc),
        }
    except RuntimeError as exc:
        return {
            'status': 'error',
            'step': 'llm_extraction',
            'extracted_json': None,
            'cost_estimate_eur': 0.0,
            'pages_analyzed': pages_extracted,
            'pages_total': 0,
            'is_truncated': is_truncated,
            'is_scanned': scanned,
            'warnings': warnings,
            'error': str(exc),
        }
    except Exception as exc:
        return {
            'status': 'error',
            'step': 'llm_extraction',
            'extracted_json': None,
            'cost_estimate_eur': 0.0,
            'pages_analyzed': pages_extracted,
            'pages_total': 0,
            'is_truncated': is_truncated,
            'is_scanned': scanned,
            'warnings': warnings,
            'error': f'Unexpected error: {exc}',
        }

    # Merge warnings from extraction into top-level warnings
    extraction_warnings = extracted.pop('warnings', []) or []
    all_warnings = warnings + extraction_warnings

    cost = _cost_eur(tokens)
    status = 'partial' if (is_truncated or scanned) else 'ok'

    return {
        'status': status,
        'step': None,
        'extracted_json': extracted,
        'cost_estimate_eur': cost,
        'pages_analyzed': pages_extracted,
        'pages_total': 0,  # populated by caller if known
        'is_truncated': is_truncated,
        'is_scanned': scanned,
        'warnings': all_warnings,
        'error': None,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Analyze a tender PDF')
    parser.add_argument('pdf', help='Path to tender PDF')
    parser.add_argument('--notice-id', default='', help='TED notice ID (optional)')
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f'Error: {pdf_path} not found', file=sys.stderr)
        sys.exit(1)

    # Load .env if present
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                if k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

    result = analyze_tender(pdf_path.read_bytes(), notice_id=args.notice_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['status'] in ('ok', 'partial') else 1)
