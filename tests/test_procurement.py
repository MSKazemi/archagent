#!/usr/bin/env python3
"""
Tests for archagent/intelligence/procurement.py — Italian procurement domain logic.

Pure unit tests, no external deps. Run:
  python3 tests/test_procurement.py
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import unittest

from archagent.intelligence import procurement as p


class TestSOA(unittest.TestCase):
    def test_catalog_complete(self):
        cat = p.soa_category_catalog()
        codes = {c['code'] for c in cat}
        self.assertIn('OG1', codes)
        self.assertIn('OG11', codes)
        self.assertIn('OS28', codes)
        self.assertIn('OS35', codes)
        self.assertEqual(len(cat), len(p.SOA_CATEGORIES))
        # types are correctly labelled
        self.assertEqual(next(c for c in cat if c['code'] == 'OG1')['type'], 'OG')
        self.assertEqual(next(c for c in cat if c['code'] == 'OS3')['type'], 'OS')

    def test_soa_rank(self):
        self.assertEqual(p.soa_rank('I'), 0)
        self.assertEqual(p.soa_rank('VIII'), 9)
        self.assertEqual(p.soa_rank('III-bis'), 3)
        self.assertEqual(p.soa_rank('nonsense'), -1)
        self.assertEqual(p.soa_rank(''), -1)

    def test_infer_soa_class(self):
        self.assertIsNone(p.infer_soa_class(0))
        self.assertIsNone(p.infer_soa_class(100_000))   # below €150K threshold
        self.assertEqual(p.infer_soa_class(200_000), 'I')      # ≤ 258K
        self.assertEqual(p.infer_soa_class(500_000), 'II')     # ≤ 516K
        self.assertEqual(p.infer_soa_class(1_000_000), 'III')  # ≤ 1.033M
        self.assertEqual(p.infer_soa_class(34_000_000), 'VIII')  # unlimited
        self.assertIsNone(p.infer_soa_class(None))
        self.assertIsNone(p.infer_soa_class('not-a-number'))

    def test_valid_category(self):
        self.assertTrue(p.is_valid_soa_category('OG1'))
        self.assertTrue(p.is_valid_soa_category('og11'))
        self.assertFalse(p.is_valid_soa_category('OG99'))


class TestCIG(unittest.TestCase):
    def test_is_valid_cig(self):
        self.assertTrue(p.is_valid_cig('A1234B5678'))
        self.assertTrue(p.is_valid_cig('9876543210'))
        self.assertFalse(p.is_valid_cig('ABCDEFGHIJ'))   # no digit
        self.assertFalse(p.is_valid_cig('A1234B567'))    # 9 chars
        self.assertFalse(p.is_valid_cig('A1234B56789'))  # 11 chars
        self.assertFalse(p.is_valid_cig(''))
        self.assertFalse(p.is_valid_cig(None))

    def test_extract_cig_labelled(self):
        self.assertEqual(
            p.extract_cig('Procedura aperta CIG: A1234B5678 per lavori.'), 'A1234B5678')
        self.assertEqual(
            p.extract_cig('C.I.G. 9988776655 — appalto integrato'), '9988776655')

    def test_extract_cig_fallback(self):
        # No label, but a valid standalone token
        self.assertEqual(p.extract_cig('riferimento gara B1A2C3D4E5 valore €1M'), 'B1A2C3D4E5')

    def test_extract_cig_none(self):
        self.assertIsNone(p.extract_cig('nessun codice qui presente oggi'))
        self.assertIsNone(p.extract_cig(''))
        self.assertIsNone(p.extract_cig(None))


class TestCauzioni(unittest.TestCase):
    def test_provvisoria_standard(self):
        r = p.cauzione_provvisoria(900_000)
        self.assertEqual(r['standard_eur'], 18_000.0)
        self.assertEqual(r['amount_eur'], 18_000.0)
        self.assertEqual(r['reduction_pct'], 0.0)

    def test_provvisoria_iso9001(self):
        r = p.cauzione_provvisoria(900_000, ['ISO 9001'])
        self.assertEqual(r['reduction_pct'], 50.0)
        self.assertEqual(r['amount_eur'], 9_000.0)

    def test_provvisoria_additive(self):
        # ISO 9001 (50%) + ISO 14001 (20%) = 70% → €5,400 on €18,000
        r = p.cauzione_provvisoria(900_000, ['ISO 9001:2015', 'ISO 14001'])
        self.assertEqual(r['reduction_pct'], 70.0)
        self.assertEqual(r['amount_eur'], 5_400.0)

    def test_provvisoria_cap(self):
        r = p.cauzione_provvisoria(1_000_000,
                                   ['ISO 9001', 'ISO 14001', 'SA8000', 'ISO 45001'])
        # 50+20+10+5 = 85% capped at 80%
        self.assertEqual(r['reduction_pct'], 80.0)
        self.assertAlmostEqual(r['amount_eur'], 4_000.0)

    def test_definitiva_standard(self):
        r = p.cauzione_definitiva(850_000, 0)
        self.assertEqual(r['percent'], 10.0)
        self.assertEqual(r['amount_eur'], 85_000.0)

    def test_definitiva_ribasso_surcharge(self):
        # ribasso 18% → +8 points × 2% = +16% → 26% of 850K = 221,000
        r = p.cauzione_definitiva(850_000, 18)
        self.assertEqual(r['percent'], 26.0)
        self.assertEqual(r['amount_eur'], 221_000.0)

    def test_definitiva_cap_30(self):
        r = p.cauzione_definitiva(1_000_000, 40)  # would be 70% → capped 30%
        self.assertEqual(r['percent'], 30.0)
        self.assertEqual(r['amount_eur'], 300_000.0)


class TestDocuments(unittest.TestCase):
    def test_required_documents_basic(self):
        docs = p.required_documents({'value_eur': 900_000})
        names = {d['name'] for d in docs}
        self.assertIn('DURC', names)
        self.assertIn('PASSOE / FVOE', names)
        self.assertIn('Attestazione SOA', names)
        self.assertIn('Cauzione provvisoria', names)
        durc = next(d for d in docs if d['name'] == 'DURC')
        self.assertIn('120', durc['validity'])

    def test_soa_doc_conditional(self):
        # Below SOA threshold → SOA not mandatory
        small = p.required_documents({'value_eur': 100_000})
        soa = next(d for d in small if d['name'] == 'Attestazione SOA')
        self.assertFalse(soa['mandatory'])
        big = p.required_documents({'value_eur': 900_000})
        soa = next(d for d in big if d['name'] == 'Attestazione SOA')
        self.assertTrue(soa['mandatory'])

    def test_dlgs36_checklist(self):
        chk = p.dlgs36_checklist({'value_eur': 900_000, 'cig': 'A1234B5678'})
        items = {c['item']: c for c in chk}
        self.assertEqual(items['CIG identificato']['status'], 'ok')
        self.assertIn('III', items['Classifica SOA richiesta']['detail'])

    def test_dlgs36_no_cig(self):
        chk = p.dlgs36_checklist({'value_eur': 200_000})
        items = {c['item']: c for c in chk}
        self.assertEqual(items['CIG identificato']['status'], 'todo')


class TestPNRR(unittest.TestCase):
    def test_detect_pnrr_positive(self):
        r = p.detect_pnrr('Intervento finanziato da fondi PNRR Missione M2C3 efficientamento')
        self.assertTrue(r['is_pnrr'])
        self.assertIn('pnrr', r['signals'])
        self.assertIsNotNone(r['mission'])

    def test_detect_pnrr_negative(self):
        r = p.detect_pnrr('Ordinaria manutenzione stradale comunale')
        self.assertFalse(r['is_pnrr'])
        self.assertEqual(r['signals'], [])

    def test_detect_pnrr_superbonus(self):
        r = p.detect_pnrr('lavori superbonus su edificio residenziale')
        self.assertTrue(r['is_pnrr'])


class TestDeadline(unittest.TestCase):
    def test_critical(self):
        r = p.deadline_status('2026-06-01', today_iso='2026-05-30')
        self.assertEqual(r['days_remaining'], 2)
        self.assertEqual(r['urgency'], 'critical')

    def test_urgent(self):
        r = p.deadline_status('2026-06-08', today_iso='2026-05-30')
        self.assertEqual(r['urgency'], 'urgent')

    def test_comfortable(self):
        r = p.deadline_status('2026-08-01', today_iso='2026-05-30')
        self.assertEqual(r['urgency'], 'comfortable')

    def test_expired(self):
        r = p.deadline_status('2026-05-01', today_iso='2026-05-30')
        self.assertEqual(r['urgency'], 'expired')

    def test_missing(self):
        self.assertEqual(p.deadline_status('')['urgency'], 'unknown')
        self.assertEqual(p.deadline_status('garbage')['urgency'], 'unknown')


class TestGoNoGo(unittest.TestCase):
    def test_go(self):
        d = p.deadline_status('2026-08-01', today_iso='2026-05-30')
        r = p.go_no_go(85, d, value_match=True)
        self.assertEqual(r['decision'], 'GO')

    def test_conditional(self):
        d = p.deadline_status('2026-08-01', today_iso='2026-05-30')
        r = p.go_no_go(60, d, value_match=True)
        self.assertEqual(r['decision'], 'CONDITIONAL GO')

    def test_no_go_low_score(self):
        r = p.go_no_go(30, {'urgency': 'comfortable'})
        self.assertEqual(r['decision'], 'NO-GO')

    def test_no_go_expired(self):
        r = p.go_no_go(95, {'urgency': 'expired'})
        self.assertEqual(r['decision'], 'NO-GO')

    def test_conditional_critical_becomes_nogo(self):
        r = p.go_no_go(60, {'urgency': 'critical'})
        self.assertEqual(r['decision'], 'NO-GO')

    def test_value_mismatch_downgrades(self):
        r = p.go_no_go(90, {'urgency': 'comfortable'}, value_match=False)
        self.assertEqual(r['decision'], 'CONDITIONAL GO')


class TestHandlers(unittest.TestCase):
    def test_soa_categories_handler(self):
        from archagent.api.handlers import procurement as h
        out = h.soa_categories()
        self.assertGreaterEqual(out['total'], 50)
        self.assertEqual(out['items'][0]['code'], 'OG1')
        self.assertTrue(any(c['code'] == 'VIII' and c['max_value_eur'] is None
                            for c in out['classifications']))

    def test_analyze_text_handler(self):
        from archagent.api.handlers import procurement as h
        out = h.analyze_text({
            'text': 'Procedura aperta CIG: A1234B5678 efficientamento energetico PNRR M2C3',
            'value_eur': 900_000, 'deadline_date': '2026-09-01',
            'certifications': ['ISO 9001'], 'ribasso_pct': 18,
        })
        self.assertEqual(out['cig'], 'A1234B5678')
        self.assertEqual(out['inferred_soa_class'], 'III')
        self.assertEqual(out['cauzione_provvisoria']['amount_eur'], 9_000.0)
        self.assertEqual(out['cauzione_definitiva']['percent'], 26.0)
        self.assertTrue(out['pnrr']['is_pnrr'])
        self.assertIn(out['go_no_go']['decision'], ('GO', 'CONDITIONAL GO', 'NO-GO'))

    def test_analyze_text_empty(self):
        from archagent.api.handlers import procurement as h
        out = h.analyze_text({})
        self.assertIsNone(out['cig'])
        self.assertIsNone(out['inferred_soa_class'])


if __name__ == '__main__':
    import unittest as _u
    result = _u.main(exit=False, verbosity=2).result
    if result.wasSuccessful():
        print('\nPASS tests/test_procurement.py: all procurement domain tests OK')
    else:
        _sys.exit(1)
