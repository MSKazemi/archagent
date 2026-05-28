#!/usr/bin/env python3
"""
Tests for analyst.py and compliance_agent.py.

Unit tests run without AZURE_OPENAI_KEY (mocked).
Integration test runs only when AZURE_OPENAI_KEY is set.

Run:
  python3 test_analyst.py              # unit tests only
  AZURE_OPENAI_KEY=... python3 test_analyst.py  # + integration
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent))

import analyst
import compliance_agent


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fake_azure_response(content: dict, tokens: int = 500) -> tuple[dict, int]:
    """Simulate a successful _call_azure response."""
    resp = {
        'choices': [{'message': {'content': json.dumps(content)}}],
        'usage': {'total_tokens': tokens},
    }
    return resp, tokens


def _fake_azure_extraction(**overrides) -> dict:
    base = {
        'buyer': 'Comune di Bologna',
        'tender_title': 'Riqualificazione energetica scuola primaria',
        'deadline': '2026-08-31T12:00:00',
        'value_eur': 1500000,
        'cpv_codes': ['45331100-7'],
        'soa_required': [{'category': 'OG11', 'classification': 'IV'}],
        'certifications_required': ['ISO 9001:2015'],
        'evaluation_criteria': {'technical': 70, 'price': 30},
        'submission_format': 'telematica',
        'lot_count': 1,
        'geographic_scope': 'Emilia-Romagna',
        'warnings': [],
    }
    base.update(overrides)
    return base


# ─── analyst.py unit tests ────────────────────────────────────────────────────

class TestExtractText(unittest.TestCase):

    def _make_pdf_bytes(self) -> bytes:
        # Minimal valid-ish PDF bytes (pdftotext will fail gracefully)
        return b'%PDF-1.4 fake content'

    @patch('analyst.subprocess.run')
    def test_digital_pdf_text_extracted(self, mock_run):
        """pdftotext returns real text → not scanned."""
        mock_run.side_effect = [
            MagicMock(stdout=b'Articolo 1. Oggetto del contratto\n' * 200, returncode=0),  # pdftotext
            MagicMock(stdout=b'Pages: 80\n', returncode=0),   # pdfinfo
        ]
        with patch('analyst.tempfile.NamedTemporaryFile') as mock_tmp:
            mock_tmp.return_value.__enter__ = lambda s: s
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            mock_tmp.return_value.name = '/tmp/fake.pdf'
            with patch('analyst.Path.unlink'):
                text, pages, truncated = analyst._extract_text(self._make_pdf_bytes())
        self.assertGreater(len(text), 100)
        self.assertEqual(pages, 80)
        self.assertFalse(truncated)

    @patch('analyst.subprocess.run')
    def test_scanned_detection(self, mock_run):
        """pdftotext returns sparse text → _is_scanned returns True."""
        mock_run.side_effect = [
            MagicMock(stdout=b'   \n\n   \n', returncode=0),  # pdftotext: nearly empty
            MagicMock(stdout=b'Pages: 100\n', returncode=0),
        ]
        with patch('analyst.tempfile.NamedTemporaryFile') as mock_tmp:
            mock_tmp.return_value.__enter__ = lambda s: s
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            mock_tmp.return_value.name = '/tmp/fake.pdf'
            with patch('analyst.Path.unlink'):
                text, pages, _ = analyst._extract_text(self._make_pdf_bytes())
        self.assertTrue(analyst._is_scanned(text, pages))

    def test_150_page_cap_triggers_truncation_warning(self):
        """analyze_tender result contains truncation warning for >150 page docs."""
        long_text = 'Articolo 1. Requisiti SOA OG11 Classifica IV\n' * 4000  # >> CHUNK_CHARS

        with patch('analyst._extract_text', return_value=(long_text, 150, True)), \
             patch('analyst._is_scanned', return_value=False), \
             patch('analyst._call_azure', return_value=_fake_azure_response(
                 _fake_azure_extraction())):
            result = analyst.analyze_tender(b'fake', notice_id='test-123')

        self.assertTrue(result['is_truncated'])
        self.assertTrue(any('truncated' in w.lower() for w in result['warnings']))
        self.assertEqual(result['status'], 'partial')


class TestChunking(unittest.TestCase):

    def test_short_text_single_chunk(self):
        text = 'x' * 1000
        chunks = analyst._chunk_text(text)
        self.assertEqual(len(chunks), 1)

    def test_long_text_multiple_chunks(self):
        text = ('Articolo 1.\n\n' * 20000)  # well over CHUNK_CHARS
        chunks = analyst._chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), analyst.CHUNK_CHARS + 500)

    def test_chunks_cover_full_text(self):
        text = 'abcdef\n\n' * 30000
        chunks = analyst._chunk_text(text)
        reconstructed = ''.join(chunks)
        # No characters should be lost
        self.assertEqual(len(reconstructed), len(text))


class TestErrorHandling(unittest.TestCase):

    def test_pdf_too_large_returns_error_step(self):
        big_bytes = b'x' * (analyst.MAX_PDF_BYTES + 1)
        result = analyst.analyze_tender(big_bytes)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['step'], 'size_check')
        self.assertIsNone(result['extracted_json'])

    @patch('analyst._extract_text', side_effect=RuntimeError('pdftotext not found'))
    def test_pdftotext_missing_returns_named_step(self, _):
        result = analyst.analyze_tender(b'fake')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['step'], 'text_extraction')
        self.assertIn('pdftotext', result['error'])

    @patch('analyst._extract_text', return_value=('Some text content ' * 100, 10, False))
    @patch('analyst._is_scanned', return_value=False)
    @patch('analyst._call_azure', side_effect=RuntimeError('Azure API HTTP 429: rate limit'))
    def test_azure_api_error_returns_llm_step(self, *_):
        result = analyst.analyze_tender(b'fake')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['step'], 'llm_extraction')
        self.assertIn('429', result['error'])

    @patch('analyst._extract_text', return_value=('Some text content ' * 100, 10, False))
    @patch('analyst._is_scanned', return_value=False)
    @patch('analyst._extract_text')
    def test_missing_azure_key_returns_config_step(self, *_):
        with patch.dict(os.environ, {'AZURE_OPENAI_KEY': '', 'AZURE_OPENAI_ENDPOINT': ''}):
            # Reset the env check by patching _azure_endpoint to raise
            with patch('analyst._azure_endpoint', side_effect=EnvironmentError('AZURE_OPENAI_KEY required')):
                with patch('analyst._extract_text', return_value=('text ' * 100, 10, False)), \
                     patch('analyst._is_scanned', return_value=False):
                    result = analyst.analyze_tender(b'fake')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['step'], 'llm_config')


class TestSuccessPath(unittest.TestCase):

    @patch('analyst._extract_text', return_value=('Requisiti SOA OG11 Classifica IV\n' * 500, 80, False))
    @patch('analyst._is_scanned', return_value=False)
    @patch('analyst._call_azure')
    def test_ok_result_has_required_fields(self, mock_azure, *_):
        mock_azure.return_value = _fake_azure_response(_fake_azure_extraction())
        result = analyst.analyze_tender(b'fake', notice_id='332586-2026')
        self.assertEqual(result['status'], 'ok')
        self.assertIsNone(result['error'])
        self.assertIn('buyer', result['extracted_json'])
        self.assertIn('soa_required', result['extracted_json'])
        self.assertIsInstance(result['cost_estimate_eur'], float)
        self.assertGreater(result['cost_estimate_eur'], 0)

    @patch('analyst._extract_text', return_value=('text ' * 100, 80, False))
    @patch('analyst._is_scanned', return_value=False)
    @patch('analyst._call_azure')
    def test_malformed_json_adds_warning_not_crash(self, mock_azure, *_):
        resp = {
            'choices': [{'message': {'content': 'NOT JSON AT ALL'}}],
            'usage': {'total_tokens': 100},
        }
        mock_azure.return_value = (resp, 100)
        result = analyst.analyze_tender(b'fake')
        # Should not raise; should return partial result with warning
        self.assertNotEqual(result['status'], 'error')
        extracted = result['extracted_json']
        self.assertIsNotNone(extracted)
        warnings = result.get('warnings', []) or extracted.get('warnings', [])
        self.assertTrue(any('malformed' in w.lower() or 'JSON' in w for w in warnings))


# ─── compliance_agent.py unit tests ───────────────────────────────────────────

class TestSOAMatching(unittest.TestCase):

    def _profile(self, soa_qualifications, **kwargs) -> dict:
        base = {
            'company_name': 'Test SRL',
            'soa_qualifications': json.dumps(soa_qualifications),
            'ateco_codes': '[]',
            'certifications_held': '[]',
            'geographic_regions': '[]',
            'avg_project_value_eur': None,
        }
        base.update(kwargs)
        return base

    def _extraction(self, soa_required, **kwargs) -> dict:
        base = {
            'soa_required': soa_required,
            'certifications_required': [],
            'geographic_scope': None,
            'value_eur': None,
        }
        base.update(kwargs)
        return base

    def test_exact_soa_match(self):
        profile = self._profile([{'category': 'OG11', 'classification': 'IV'}])
        extracted = self._extraction([{'category': 'OG11', 'classification': 'IV'}])
        result = compliance_agent.check_compliance(extracted, profile)
        self.assertEqual(result['gaps'], [])
        self.assertGreater(result['bid_readiness_score'], 80)

    def test_higher_classification_satisfies_lower_requirement(self):
        # Company has OG11-V (higher), tender needs OG11-IV (lower) → OK
        profile = self._profile([{'category': 'OG11', 'classification': 'V'}])
        extracted = self._extraction([{'category': 'OG11', 'classification': 'IV'}])
        result = compliance_agent.check_compliance(extracted, profile)
        gap_cats = [g for g in result['gaps'] if 'OG11' in g]
        self.assertEqual(gap_cats, [], 'V satisfies IV requirement')

    def test_lower_classification_creates_gap(self):
        # Company has OG11-III, tender needs OG11-V → GAP
        profile = self._profile([{'category': 'OG11', 'classification': 'III'}])
        extracted = self._extraction([{'category': 'OG11', 'classification': 'V'}])
        result = compliance_agent.check_compliance(extracted, profile)
        soa_gaps = [g for g in result['gaps'] if 'OG11' in g]
        self.assertTrue(len(soa_gaps) > 0, 'Should flag OG11 classification gap')
        self.assertIn('III', soa_gaps[0])
        self.assertIn('V', soa_gaps[0])

    def test_missing_soa_category(self):
        profile = self._profile([{'category': 'OG1', 'classification': 'IV'}])
        extracted = self._extraction([{'category': 'OG11', 'classification': 'IV'}])
        result = compliance_agent.check_compliance(extracted, profile)
        gaps = [g for g in result['gaps'] if 'OG11' in g]
        self.assertTrue(len(gaps) > 0)

    def test_no_soa_required_full_score(self):
        profile = self._profile([])
        extracted = self._extraction([])
        result = compliance_agent.check_compliance(extracted, profile)
        self.assertGreaterEqual(result['bid_readiness_score'], 80)

    def test_missing_profile_returns_zero_score(self):
        result = compliance_agent.check_compliance({}, None)
        self.assertEqual(result['bid_readiness_score'], 0)
        self.assertTrue(result['profile_missing'])

    def test_certification_gap(self):
        profile = self._profile(
            [],
            certifications_held=json.dumps(['ISO 9001:2015']),
        )
        extracted = self._extraction(
            [],
            certifications_required=['ISO 9001:2015', 'ISO 14001'],
        )
        result = compliance_agent.check_compliance(extracted, profile)
        cert_gaps = [g for g in result['gaps'] if '14001' in g]
        self.assertTrue(len(cert_gaps) > 0)

    def test_geographic_mismatch(self):
        profile = self._profile(
            [],
            geographic_regions=json.dumps(['ITA-SI']),  # Sicilia only
        )
        extracted = self._extraction([], geographic_scope='Lombardia')
        result = compliance_agent.check_compliance(extracted, profile)
        geo_gaps = [g for g in result['gaps'] if 'Lombardia' in g or 'geographic' in g.lower()]
        self.assertTrue(len(geo_gaps) > 0)


# ─── Integration test (requires AZURE_OPENAI_KEY) ─────────────────────────────

class TestIntegration(unittest.TestCase):

    @unittest.skipUnless(os.environ.get('AZURE_OPENAI_KEY'), 'AZURE_OPENAI_KEY not set')
    def test_real_azure_call_with_sample_text(self):
        """Call real Azure API with a minimal Italian tender excerpt."""
        sample_text = textwrap.dedent("""
            BANDO DI GARA - PROCEDURA APERTA
            Stazione appaltante: Comune di Bologna
            Oggetto: Lavori di riqualificazione energetica
            Importo a base d'asta: € 1.500.000,00
            CIG: 9876543210
            Termine presentazione offerte: 31/08/2026 ore 12:00

            Requisiti di qualificazione:
            - Attestazione SOA categoria OG11 classifica IV o superiore
            - Certificazione ISO 9001:2015
            Criterio di aggiudicazione: offerta economicamente più vantaggiosa
            Punteggio tecnico: 70 punti
            Punteggio economico: 30 punti
        """)

        with patch('analyst._extract_text', return_value=(sample_text, 5, False)), \
             patch('analyst._is_scanned', return_value=False):
            result = analyst.analyze_tender(b'fake_for_integration_test', notice_id='integration-test')

        self.assertIn(result['status'], ('ok', 'partial'))
        self.assertIsNotNone(result['extracted_json'])
        extracted = result['extracted_json']
        self.assertIsNotNone(extracted.get('buyer') or extracted.get('value_eur'))
        print(f'\n[Integration] cost_estimate_eur={result["cost_estimate_eur"]}')
        print(f'[Integration] extracted: {json.dumps(extracted, ensure_ascii=False, indent=2)}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
