import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

P=Path(__file__).resolve().parents[1]/'scripts'/'collect.py'
spec=importlib.util.spec_from_file_location('collector_quality',P)
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

class QualityTests(unittest.TestCase):
    def make(self,title,link,day=None,source='OpenAI 공식 발표',kind=None):
        d=day or c.NOW.date().isoformat()
        x=c.create_item(title,link,d,'Summary',source,'해외','모델 출시')
        if x and kind:x['sourceType']=kind
        return x

    def test_codex_latest_payload_cap(self):
        release={'id':9,'draft':False,'prerelease':False,
                 'published_at':c.NOW.isoformat(), 'html_url':'https://github.com/openai/codex/releases/tag/v2',
                 'name':'v2','body':'notes'}
        with patch.object(c,'fetch',return_value=json.dumps(release).encode()) as f:
            rows=c.collect_releases('openai/codex','Codex','코딩 도구')
        self.assertEqual(len(rows),1)
        self.assertEqual(f.call_args.kwargs.get('max_bytes'), 12_000_000)
        self.assertIn('/releases/latest',f.call_args.args[0])

    def test_related_links_survive_recollection(self):
        primary=self.make('Claude Code adds parallel subagents for coding tasks',
                          'https://official.ai/claude-parallel',source='Official')
        secondary=self.make('Claude Code adds parallel subagents for coding tasks',
                            'https://news.kr/story',source='AI타임스 (보도)',kind='secondary')
        first=c.dedup_merge([secondary],[primary])
        second=c.dedup_merge(first,[primary])
        self.assertEqual(len(second),1)
        self.assertEqual(len(second[0]['urls']),2)
        self.assertEqual(len(second[0]['relatedSources']),2)

    def test_new_window(self):
        fresh=(c.NOW.date()-dt.timedelta(days=3)).isoformat()
        stale=(c.NOW.date()-dt.timedelta(days=8)).isoformat()
        self.assertIsNotNone(self.make('AI model released','https://foo.org/now',fresh))
        self.assertIsNone(self.make('AI model released','https://foo.org/old',stale))
        self.assertIsNone(c.create_item('AI model released','https://foo.org/nodate','', 'Summary','Official','해외'))

    def test_archive_is_shorter(self):
        day=(c.NOW.date()-dt.timedelta(days=40)).isoformat()
        old={'id':'old','title':'Old AI item','date':day,'urls':['https://example.org/old'],'externalId':'old'}
        self.assertEqual(c.dedup_merge([old],[]),[])

    def test_near_duplicate_collapses_and_preserves_sources(self):
        off=self.make('Claude Code adds parallel subagents for coding tasks','https://official.ai/claude-parallel',source='Official')
        news=self.make('Claude Code adds parallel subagents for coding tasks','https://news.kr/story',source='AI타임스 (보도)',kind='secondary')
        a=c.dedup_merge([news],[off])
        self.assertEqual(len(a),1)
        self.assertEqual(len(a[0]['urls']),2)
        self.assertEqual(a[0]['sourceLabel'],'Official')
        self.assertEqual(set(a[0]['relatedSources']),{'Official','AI타임스 (보도)'})

    def test_version_numbers_not_smashed(self):
        v1=self.make('Codex CLI 1.2 release update','https://x.org/v1')
        v2=self.make('Codex CLI 1.3 release update','https://x.org/v2')
        self.assertFalse(c.near_duplicate(v1,v2))
        self.assertEqual(len(c.dedup_merge([v1],[v2])),2)

    def test_generic_news_filtered(self):
        date=c.NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')
        xml=f'<rss><channel><item><title>DevFest returns this summer</title><link>https://example.org/event</link><pubDate>{date}</pubDate><description>Talks about AI and coding</description></item><item><title>AI agent released with improved tools</title><link>https://example.org/agent</link><pubDate>{date}</pubDate></item></channel></rss>'.encode()
        with patch.object(c,'fetch',return_value=xml):
            rows=c.collect_rss('네이버 D2','https://x.org/rss','한국')
        self.assertEqual(len(rows),1)
        self.assertIn('AI agent',rows[0]['title'])

    def test_secondary_label(self):
        date=c.NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')
        xml=f'<rss><channel><item><title>AI 신모델 출시</title><link>https://example.org/story</link><pubDate>{date}</pubDate><description>보도 뉴스</description></item></channel></rss>'.encode()
        with patch.object(c,'fetch',return_value=xml):
            rows=c.collect_rss('AI타임스 (보도)','https://x.org/rss','한국')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['sourceType'],'secondary')
        self.assertEqual(rows[0]['reliability'],2)

    def test_retains_old_while_source_failure(self):
        day=(c.NOW.date()-dt.timedelta(days=20)).isoformat()
        old={'id':'old','title':'Old AI item','date':day,'urls':['https://example.org/old'],'externalId':'old'}
        self.assertEqual(len(c.dedup_merge([old],[])),1)

if __name__=='__main__':unittest.main()