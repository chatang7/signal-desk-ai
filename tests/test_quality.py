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

    def test_deepmind_official_feed_is_monitored(self):
        urls = {name: url for name, url, region in c.RSS}
        self.assertEqual(urls.get("Google DeepMind"), "https://deepmind.google/blog/rss.xml")
        self.assertIn("Google DeepMind", c.FOCUSED_SOURCES)

    def test_github_copilot_is_not_open_source_just_because_github(self):
        self.assertEqual(c.classify("Gemini 4 Carbon in GitHub Copilot"), "코딩 도구")
        self.assertEqual(c.classify("Gemini 4 Carbon is now available"), "모델 출시")
        self.assertEqual(c.classify("New model source code open-sourced"), "GitHub 오픈소스")

    def test_deepmind_rss_fallback_after_xml_error(self):
        day = c.NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')
        xml = (f'<rss><channel><item><title>Gemini 4 Carbon is now available</title>'
               f'<link>https://deepmind.google/blog/gemini-4-carbon-is-now-available</link>'
               f'<pubDate>{day}</pubDate></item></channel></rss>').encode()
        primary = c.RSS_ALTERNATES["Google DeepMind"]
        with patch.object(c, "fetch", side_effect=[b"not xml", xml]) as mocked:
            rows = c.collect_rss_with_fallback(
                "Google DeepMind", "https://deepmind.google/blog/rss.xml", "해외")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sourceLabel"], "Google DeepMind")
        self.assertEqual(mocked.call_args.args[0], primary)
        self.assertEqual(rows[0]["title"], "Gemini 4 Carbon is now available")

    def test_article_media_image_from_rss_is_preserved(self):
        day = c.NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')
        xml = ('<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>'
               '<image><url>https://example.org/channel-logo.png</url></image>'
               '<item><title>New AI model announced</title>'
               '<link>https://blog.google/news/item</link>'
               f'<pubDate>{day}</pubDate>'
               '<media:content url="https://blog.google/media/cover.jpg" type="image/jpeg" />'
               '</item></channel></rss>').encode()
        with patch.object(c, "fetch", return_value=xml):
            rows = c.collect_rss("Google AI 블로그", "https://feed.example/rss", "해외")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["imageUrl"], "https://blog.google/media/cover.jpg")

    def test_image_enclosure_is_accepted_only_for_images(self):
        import xml.etree.ElementTree as ET
        el = ET.fromstring('<item><enclosure url="https://official.test/evil.mp4" '
                           'type="video/mp4" /><enclosure url="https://official.test/cover.png" '
                           'type="image/png" /></item>')
        self.assertEqual(c.rss_article_image(el), "https://official.test/cover.png")
        empty = ET.fromstring('<item><enclosure url="https://official.test/clip.mp4" '
                              'type="video/mp4" /></item>')
        self.assertEqual(c.rss_article_image(empty), "")

    def test_recollection_keeps_prior_image_when_feed_omits_one(self):
        old = self.make("New AI model announced", "https://blog.google/news/unchanged")
        new = self.make("New AI model announced", "https://blog.google/news/unchanged")
        old["imageUrl"] = "https://blog.google/media/cover.jpg"
        self.assertEqual(c.dedup_merge([old], [new])[0]["imageUrl"],
                         "https://blog.google/media/cover.jpg")

    def test_retains_old_while_source_failure(self):
        day=(c.NOW.date()-dt.timedelta(days=20)).isoformat()
        old={'id':'old','title':'Old AI item','date':day,'urls':['https://example.org/old'],'externalId':'old'}
        self.assertEqual(len(c.dedup_merge([old],[])),1)

if __name__=='__main__':unittest.main()