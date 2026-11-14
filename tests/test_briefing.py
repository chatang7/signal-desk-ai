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

    def test_major_frontier_launch_beats_routine_features(self):
        carbon = story("Gemini 4 Carbon is now available", source="Google DeepMind",
                       summary="Google is making its strongest model available to developers and consumers.")
        feature = story("Gemini Intelligence: New features, on more devices",
                        source="Google AI 블로그",
                        summary="Gemini app includes new Android intelligence features and settings.")
        integration = story("Gemini 4 Carbon in GitHub Copilot",
                            topic="코딩 도구", source="GitHub Copilot",
                            summary="Google model available inside the coding assistant.")
        generic = story("Google Cloud launches Gemini for healthcare",
                        source="Google AI 블로그",
                        summary="Gemini-powered healthcare services are expanding for businesses.")
        self.assertTrue(b.major_model_launch(carbon))
        self.assertFalse(b.major_model_launch(feature))
        self.assertFalse(b.major_model_launch(integration))
        self.assertFalse(b.major_model_launch(generic))
        self.assertGreater(b.score(carbon), b.score(feature))
        self.assertEqual(b.evidence_draft(carbon)["majorLaunch"], True)

        others = [story("Other AI update " + str(i), topic="코딩 도구",
                        source="Company " + str(i),
                        summary="This is a routine update for software teams and developers.")
                  for i in range(12)]
        selected = b.choose(others + [carbon], limit=8)
        self.assertIn(carbon, selected)
        self.assertEqual(selected[0], carbon)

    def test_major_launch_rejects_secondary_news_without_official_confirmation(self):
        a = story("Gemini 4 Carbon is now available", source="Google DeepMind")
        a["sourceType"] = "secondary"
        self.assertFalse(b.major_model_launch(a))
        a["sourceType"] = "primary"
        a["sourceLabel"] = "AI타임스 (보도)"
        self.assertFalse(b.major_model_launch(a))

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
        prev={'items':[{**b.evidence_draft(s),'fingerprint':b.fingerprint(s),'mode':'ai-assisted','summary':'검증된 요약',
                        'voiceMood':'curious','voiceHook':'흥미로운 새 움직임이야.','voiceTake':'원문에서 확인해야 할 구체적인 쟁점이 있네.'}]}
        with patch.object(b,'_json_request',side_effect=AssertionError('cache should be used')):
            res=b.build({'items':[s]},prev,api_key='dummy')
        self.assertEqual(res['aiAnalyzed'],1)
        self.assertEqual(res['cacheHits'],1)
        self.assertEqual(res['items'][0]['summary'],'검증된 요약')

    def test_cached_summary_unchanged_when_voice_is_upgraded(self):
        sample=story()
        prev={'items':[{**b.evidence_draft(sample), 'fingerprint':b.fingerprint(sample),
                        'mode':'ai-assisted', 'summary':'이미 검증한 요약'}]}
        enhancement={'voiceMood':'curious',
                     'voiceHook':'흐응, 이번 소식은 흥미로운걸?',
                     'voiceTake':'새 도구가 정확히 무엇을 개선했는지 원문부터 보자고.',
                     'summary':'덮어쓰면 안 되는 신규 요약'}
        with patch.object(b,'_json_request',return_value=enhancement):
            result=b.build({'items':[sample]},prev,api_key='dummy')
        self.assertEqual(result['items'][0]['summary'],'이미 검증한 요약')
        self.assertEqual(result['items'][0]['voiceHook'],enhancement['voiceHook'])
        self.assertEqual(result['aiAnalyzed'],1)
        self.assertEqual(result['aiCalls'],1)

    def test_voice_backfill_failure_preserves_prior_analysis(self):
        sample=story()
        prev={'items':[{**b.evidence_draft(sample), 'fingerprint':b.fingerprint(sample),
                        'mode':'ai-assisted', 'summary':'기존 요약'}]}
        with patch.object(b,'_json_request',side_effect=ValueError('no voice')):
            result=b.build({'items':[sample]},prev,api_key='dummy')
        self.assertEqual(result['items'][0]['summary'],'기존 요약')
        self.assertEqual(result['aiAnalyzed'],1)
        self.assertEqual(result['aiErrors'],1)

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

    def test_benchmark_task_count_not_a_performance_gain(self):
        x=story(summary="We released a 100-task benchmark for AI agents.")
        d=b.evidence_draft(x)
        self.assertIn("미확인",d["performance"])

    def test_price_unit_guard_catches_per_million_to_per_token_error(self):
        source = "Gemini Carbon costs $2 per million input tokens and $10 per million output tokens."
        wrong = "입력 토큰당 2달러, 출력 토큰당 10달러"
        correct = "입력 100만 토큰당 2달러, 출력 100만 토큰당 10달러"
        self.assertIn("미확인", b.safe_generated_price_unit(wrong, source))
        self.assertEqual(b.safe_generated_price_unit(correct, source), correct)
        self.assertEqual(b.safe_generated_price_unit("무료 공개", source), "무료 공개")

    def test_unpublished_pricing_is_not_a_price(self):
        x=story(summary="The pricing were not published for the model.")
        d=b.evidence_draft(x)
        self.assertIn("미확인",d["cost"])

    def test_image_url_must_match_official_publisher_or_trusted_cdn(self):
        article = "https://deepmind.google/blog/model-launch"
        self.assertEqual(
            b.safe_article_image_url(article, "/images/model.webp"),
            "https://deepmind.google/images/model.webp")
        self.assertEqual(
            b.safe_article_image_url(article, "https://lh3.googleusercontent.com/model.png"),
            "https://lh3.googleusercontent.com/model.png")
        invalid = [
            "http://deepmind.google/cover.png",
            "https://deepmind.google.evil.com/cover.png",
            "https://127.0.0.1/img.jpg",
            "https://localhost/img.jpg",
            "https://deepmind.google@evil.com/img.jpg",
            "https://deepmind.google:444/img.jpg",
            "data:image/png;base64,aaa",
            "//evil.example.com/cover.png",
            "https://deepmind.google/images/cover.svg",
            "https://deepmind.google/cover.png#fragment",
        ]
        for url in invalid:
            with self.subTest(url=url):
                self.assertEqual(b.safe_article_image_url(article, url), "")
        self.assertEqual(
            b.safe_article_image_url("https://random.example.com/news", "/img.png"), "")

    def test_meta_parser_prefers_article_og_image_over_twitter_card(self):
        p = b.ArticleImageParser()
        p.feed('<html><head><meta name="twitter:image" content="/twitter.png">'
               '<meta property="og:image" content="/cover.webp">'
               '<meta property="og:image:secure_url" content="/secure.jpg">'
               '</head></html>')
        self.assertEqual(sorted(p.candidates)[0], (0, "/secure.jpg"))

    def test_rss_image_success_needs_only_head_request(self):
        article = story()
        article["imageUrl"] = "https://openai.com/images/new-model.webp"
        with patch.object(b, "confirm_image", return_value=True) as check:
            url, attribution = b.official_article_image(article)
        self.assertEqual(url, article["imageUrl"])
        self.assertEqual(attribution, "RSS 이미지")
        self.assertEqual(check.call_count, 1)

    def test_no_reachable_image_falls_back_to_text(self):
        article = story()
        article["imageUrl"] = "https://bad.example.com/picture.png"
        with patch.object(b.urllib.request, "build_opener", side_effect=OSError("offline")):
            self.assertEqual(b.official_article_image(article), ("", ""))

    def test_image_content_type_and_length_are_checked(self):
        class Response:
            def __init__(self, mime, size):
                self.headers = {"Content-Type": mime, "Content-Length": size}
            def geturl(self):
                return "https://openai.com/images/model.jpg"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class Opener:
            response = None
            def open(self, req, timeout=0):
                self.request = req
                return self.response
        opener = Opener()
        article = "https://openai.com/index/model"
        with patch.object(b.urllib.request, "build_opener", return_value=opener):
            opener.response = Response("image/jpeg", "20000")
            self.assertTrue(b.confirm_image("https://openai.com/images/model.jpg", article))
            self.assertEqual(opener.request.get_method(), "HEAD")
            opener.response = Response("text/html", "20000")
            self.assertFalse(b.confirm_image("https://openai.com/images/model.jpg", article))
            opener.response = Response("image/jpeg", "10000000")
            self.assertFalse(b.confirm_image("https://openai.com/images/model.jpg", article))

    def test_image_redirect_to_other_host_is_rejected(self):
        class RedirectRequest:
            full_url = "https://openai.com/images/model.jpg"
        guard = b.SourceRedirectGuard(lambda url: bool(
            b.safe_article_image_url("https://openai.com/index/model", url)))
        with self.assertRaises(ValueError):
            guard.redirect_request(
                RedirectRequest(), None, 302, "Found", {},
                "https://127.0.0.1/private.png")

    def test_image_is_carried_through_build_and_cached(self):
        article = story()
        article["imageUrl"] = "https://openai.com/images/new-model.jpg"
        resolved = ("https://openai.com/images/new-model.jpg", "공식 원문 대표 이미지")
        with patch.object(b, "official_article_image", return_value=resolved) as resolver:
            first = b.build({"items": [article]}, enrich_images=True)
        self.assertEqual(first["items"][0]["imageUrl"], resolved[0])
        self.assertEqual(first["items"][0]["imageSource"], resolved[1])
        self.assertEqual(resolver.call_count, 1)
        with patch.object(b, "official_article_image", side_effect=AssertionError("cache bypass")):
            second = b.build({"items": [article]}, previous=first, enrich_images=True)
        self.assertEqual(second["items"][0]["imageUrl"], resolved[0])

    def test_article_figures_provide_fallback_when_og_image_is_missing(self):
        parser = b.ArticleImageParser()
        parser.feed('<html><img src="/site-logo.png">'
                    '<figure><picture><img data-src="https://developer-blogs.nvidia.com/'
                    'wp-content/uploads/2026/11/cudla-figure.png" alt="cuDLA pipeline">'
                    '</picture></figure></html>')
        self.assertEqual(parser.candidates, [
            (4, "https://developer-blogs.nvidia.com/wp-content/uploads/2026/11/cudla-figure.png")
        ])

    def test_nvidia_owned_media_host_is_accepted_not_arbitrary_host(self):
        page = "https://developer.nvidia.com/blog/run-native-dla-inference"
        asset = "https://developer-blogs.nvidia.com/wp-content/uploads/2026/11/dla.png"
        self.assertEqual(b.safe_article_image_url(page, asset), asset)
        self.assertEqual(b.safe_article_image_url(page, "https://developer-blogs.nvidia.com.bad.org/figure.png"), "")

    def test_article_html_figure_image_is_selected_after_metadata_failure(self):
        article = {
            "urls": ["https://developer.nvidia.com/blog/cudla-python"],
            "imageUrl": "",
        }
        class FakeResponse:
            headers = {"Content-Type": "text/html; charset=utf-8"}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def geturl(self):
                return article["urls"][0]
            def read(self, n):
                return (b'<html><head><meta property="og:image" '
                        b'content="https://unsafe.example.com/blank.jpg"></head>'
                        b'<body><figure><img src="https://developer-blogs.nvidia.com/'
                        b'wp-content/uploads/2026/11/cudla.jpg"></figure></body></html>')
        class FakeOpener:
            def open(self, req, timeout=0):
                return FakeResponse()
        accepted = "https://developer-blogs.nvidia.com/wp-content/uploads/2026/11/cudla.jpg"
        with patch.object(b.urllib.request, "build_opener", return_value=FakeOpener()), \
             patch.object(b, "confirm_image", return_value=True):
            self.assertEqual(b.official_article_image(article),
                             (accepted, "공식 원문 대표 이미지"))

    def test_negative_image_cache_is_retried_on_next_collection(self):
        sample = story()
        with patch.object(b, "official_article_image", return_value=("", "")) as resolve:
            first = b.build({"items": [sample]}, enrich_images=True)
        self.assertEqual(resolve.call_count, 1)
        picture = "https://openai.com/images/new-model.jpg"
        with patch.object(b, "official_article_image",
                          return_value=(picture, "공식 원문 대표 이미지")) as resolve:
            second = b.build({"items": [sample]}, previous=first, enrich_images=True)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(second["items"][0]["imageUrl"], picture)

    def test_image_head_blocked_falls_back_to_ranged_get(self):
        from urllib.error import HTTPError
        from io import BytesIO
        article = "https://developer.nvidia.com/blog/cudla"
        img = "https://developer-blogs.nvidia.com/wp-content/uploads/dla.jpg"
        class Response:
            headers = {"Content-Type": "image/jpeg", "Content-Length": "100"}
            def __init__(self):
                self.read_calls = []
            def geturl(self):
                return img
            def read(self, size):
                self.read_calls.append(size)
                return b"\xff\xd8" + b"0" * 30
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class Opener:
            def __init__(self):
                self.methods = []
                self.response = Response()
            def open(self, req, timeout=0):
                self.methods.append((req.get_method(), req.headers.get("Range")))
                if req.get_method() == "HEAD":
                    raise HTTPError(img, 405, "HEAD blocked", {}, BytesIO())
                return self.response
        op = Opener()
        with patch.object(b.urllib.request, "build_opener", return_value=op):
            self.assertTrue(b.confirm_image(img, article))
        self.assertEqual(op.methods[0][0], "HEAD")
        self.assertEqual(op.methods[1][0], "GET")
        self.assertEqual(op.methods[1][1], "bytes=0-31")
        self.assertEqual(op.response.read_calls, [32])

    def test_no_public_api_secret_in_output(self):
        s=story()
        with patch.object(b,'_json_request',side_effect=ValueError):
            result=b.build({'items':[s]},api_key='SECRET-GEMINI-123')
        self.assertNotIn('SECRET-GEMINI-123',json.dumps(result))

if __name__=='__main__':unittest.main()