import datetime as dt
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

P=Path(__file__).resolve().parents[1]/'scripts'/'briefing.py'
spec=importlib.util.spec_from_file_location('signal_desk_briefing',P)
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

def story(title='New AI model announced',topic='모델 출시',region='해외',days=0,source='OpenAI 공식 발표',summary='Researchers have published a new AI model with new tools for developers.'):
    date=(b.NOW.date()-dt.timedelta(days=days)).isoformat()
    return {'id':f'id-{title}-{source}', 'title':title,'topic':topic,'region':region,'date':date,
            'summary':summary,'urls':['https://openai.com/index/test'],'sourceLabel':source,'reliability':4}

class BriefingTests(unittest.TestCase):
    def test_selection_excludes_old(self):
        a=story('Old report',days=20);c=story('Fresh report')
        self.assertEqual(b.choose([a,c]),[c])

    def test_selection_has_korea_inclusion(self):
        pool=[story('Overseas '+str(i),topic='모델 출시',source='Source'+str(i)) for i in range(15)]
        kr=[story('Korean '+str(i),topic='정책·규제',region='한국',source='KR'+str(i)) for i in range(2)]
        selected=b.choose(pool+kr)
        self.assertEqual(len(selected),8)
        self.assertEqual(sum(x['region']=='한국' for x in selected),2)

    def test_no_fake_prices_or_performance(self):
        d=b.evidence_draft(story(summary='AI model launched without specified cost or accuracy.'))
        self.assertIn('미확인',d['cost'])
        self.assertIn('미확인',d['performance'])
        self.assertNotEqual(d['mode'],'ai-assisted')

    def test_grounded_number(self):
        s='Official benchmark showed 27% higher accuracy than the previous version.'
        d=b.evidence_draft(story(summary=s))
        self.assertIn('27%',d['performance'])
        self.assertIn('previous',d['difference'])

    def test_undocumented_difference_stays_unknown(self):
        d=b.evidence_draft(story(summary='A brand new model was announced for developers.'))
        self.assertIn('미확인',d['difference'])

    def test_no_key_no_network_requests(self):
        with patch.object(b,'_json_request',side_effect=AssertionError('should never call')):
            res=b.build({'items':[story()]})
        self.assertEqual(res['aiAnalyzed'],0)
        self.assertEqual(res['aiCalls'],0)
        self.assertEqual(res['storyCount'],1)

    def test_cached_assistance_reused(self):
        s=story()
        prev={'items':[{**b.evidence_draft(s),'fingerprint':b.fingerprint(s),'mode':'ai-assisted','summary':'검증된 요약'}]}
        with patch.object(b,'_json_request',side_effect=AssertionError('cache should be used')):
            res=b.build({'items':[s]},prev,api_key='dummy')
        self.assertEqual(res['aiAnalyzed'],1)
        self.assertEqual(res['cacheHits'],1)
        self.assertEqual(res['items'][0]['summary'],'검증된 요약')

    def test_optional_api_recovery(self):
        s=story()
        with patch.object(b,'_json_request',side_effect=ValueError('bad output')):
            res=b.build({'items':[s]},api_key='dummy')
        self.assertEqual(res['aiAnalyzed'],0)
        self.assertEqual(res['aiErrors'],1)
        self.assertEqual(res['items'][0]['mode'],'evidence-only')

    def test_api_accepts_evidence_when_mocked(self):
        s=story()
        def good(item,key):return {'mode':'ai-assisted','summary':'신규 모델 출시','model':'gemini-2.5-flash-lite'}
        with patch.object(b,'_json_request',side_effect=good):
            res=b.build({'items':[s]},api_key='dummy')
        self.assertEqual(res['aiAnalyzed'],1)

    def test_url_host_allowlist(self):
        self.assertTrue(b.approved_source('https://openai.com/index/latest'))
        self.assertFalse(b.approved_source('https://openai.com.evil.com/scheme'))
        self.assertFalse(b.approved_source('http://openai.com/'))
        self.assertFalse(b.approved_source('https://user:password@openai.com/'))
        self.assertFalse(b.approved_source('https://127.0.0.1/'))

    def test_no_numeric_claims_created(self):
        d=b.evidence_draft(story(summary='The new model supports multimodal input and agents.'))
        self.assertNotIn('200%',d['performance'])

    def test_no_public_api_secret_in_output(self):
        s=story()
        with patch.object(b,'_json_request',side_effect=ValueError):
            result=b.build({'items':[s]},api_key='SECRET-GEMINI-123')
        self.assertNotIn('SECRET-GEMINI-123',json.dumps(result))

if __name__=='__main__':unittest.main()