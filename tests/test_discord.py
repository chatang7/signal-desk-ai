"""Offline tests for Discord schedule, dedup, rate-safety and secret handling."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "discord_post.py"
SPEC = importlib.util.spec_from_file_location("discord_post", MOD_PATH)
discord = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discord)
NOW = dt.datetime(2026, 11, 10, 9, 17, tzinfo=discord.KST)


def story(number, date="2026-11-10", score=82, source=None, topic=None):
    return {
        "id": "feed-" + str(number), "title": "AI headline " + str(number),
        "date": date, "score": score, "mode": "ai-assisted",
        "urls": ["https://example.com/article/" + str(number)],
        "sourceLabel": source or ("Source " + str(number)), "sourceType": "primary",
        "topic": topic or ("Topic " + str(number)), "summary": "Relevant AI research",
        "difference": "Compared with old method", "usage": "Validate against a local baseline.",
    }


def fresh_state():
    return {"schema": "signal-desk-discord-v1", "posted": {}, "history": []}


class DiscordTests(unittest.TestCase):
    def test_freshness_threshold(self):
        items = [story(1), story(2, "2026-11-05"), story(3, "2026-11-11"),
                 story(4, score=50), story(5), story(6)]
        selected = discord.pick(items, fresh_state(), NOW.date())
        self.assertEqual(len(selected), 1)
        self.assertIn(selected[0]["id"], {"feed-1", "feed-5", "feed-6"})

    def test_one_article_cap_even_when_multiple_eligible(self):
        for count in (1, 2, 5):
            items = [story(i) for i in range(count)]
            self.assertEqual(len(discord.pick(items, fresh_state(), NOW.date())), 1)
        self.assertEqual(discord.pick([], fresh_state(), NOW.date()), [])
        self.assertEqual(discord.pick([story(1, score=50)], fresh_state(), NOW.date()), [])

    def test_excludes_previous_deliveries(self):
        s = fresh_state()
        s["posted"]["feed-1"] = NOW.isoformat()
        selected = discord.pick([story(i) for i in range(1, 6)], s, NOW.date())
        self.assertNotIn("feed-1", {i["id"] for i in selected})

    def test_caps_source_and_topic(self):
        items = [story(i, source="one", topic="same") for i in range(6)]
        selected = discord.pick(items, fresh_state(), NOW.date())
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["sourceLabel"], "one")

    def test_cooldown_and_daily_limit(self):
        self.assertEqual(discord.MAX_SENDS_PER_DAY, 1)
        s = fresh_state()
        self.assertTrue(discord.can_send(s, NOW))
        # Even after 7+ hours have passed, the second real daily send is blocked.
        s["history"].append({"at": NOW.isoformat(), "count": 1})
        evening = NOW.replace(hour=23)
        self.assertFalse(discord.can_send(s, evening))
        # A new Korean calendar day after the cooldown becomes eligible.
        tomorrow = NOW + dt.timedelta(days=1)
        self.assertTrue(discord.can_send(s, tomorrow))
        # A prior late-night delivery can still trigger the cooldown next morning.
        s["history"] = [{"at": NOW.replace(hour=23).isoformat(), "count": 1}]
        self.assertFalse(discord.can_send(s, tomorrow.replace(hour=4)))

    def test_unknown_history_fails_closed(self):
        s = fresh_state()
        s["history"] = [{"at": "not-a-date", "count": 3}]
        self.assertFalse(discord.can_send(s, NOW))

    def test_webhook_accepted_and_rejected(self):
        url = "https://discord.com/api/webhooks/123456789/SECRET_token-ABC"
        self.assertEqual(discord.webhook_endpoint(url), url + "?wait=true")
        for bad in ["http://discord.com/api/webhooks/123/xxx",
                    "https://discord.com.evil.org/api/webhooks/123/xxx",
                    "https://evil.com/api/webhooks/123/xxx",
                    "https://discord.com/api/webhooks/123/xxx?wait=false",
                    "https://discord.com/api/webhooks/123/xxx/extra"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                discord.webhook_endpoint(bad)

    def test_payload_limits_mentions(self):
        items = [story(i) for i in range(5)]
        items[0]["summary"] = "@everyone @here " + "X" * 4000
        payload = discord.payload_for(items, NOW)
        self.assertEqual(payload["username"], "[가시춘의 홍원 가십]")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertLessEqual(len(payload["content"]), 2000)
        self.assertEqual(len(payload["embeds"]), 1)
        self.assertTrue(all(len(e["title"]) <= 256 and len(e["description"]) <= 4096
                            for e in payload["embeds"]))
        self.assertNotIn("@everyone", json.dumps(payload))
        self.assertNotIn("@here", json.dumps(payload))

    def test_no_webhook_network_during_preview(self):
        def forbidden(*args):
            raise AssertionError("Dry run should not send")
        report = discord.deliver({"items": [story(i) for i in range(5)]},
                                 fresh_state(), "", NOW, dry_run=True, sender=forbidden)
        self.assertEqual(report["status"], "preview")
        self.assertEqual(report["count"], 1)

    def test_success_records_and_repeat_skips(self):
        state = fresh_state()
        sent = []
        def fake_send(url, payload):
            sent.append(payload)
            return "123456789012345678"
        posts = [story(i) for i in range(3)]
        report = discord.deliver({"items": posts}, state,
            "https://discord.com/api/webhooks/12345/valid_token", NOW, sender=fake_send)
        self.assertEqual(report["status"], "sent")
        self.assertEqual(report["count"], 1)
        self.assertEqual(len(state["posted"]), 1)
        self.assertEqual(len(sent[0]["embeds"]), 1)
        self.assertEqual(state["history"][0]["messageId"], "123456789012345678")
        later_same_day = NOW.replace(hour=23)
        # Even if fresh items remain, a second normal delivery is forbidden.
        self.assertEqual(discord.deliver({"items": posts}, state, "", later_same_day,
                                         dry_run=True)["status"], "cooldown")
        self.assertEqual(len(sent), 1)

    def test_failed_send_keeps_ledger_unchanged(self):
        state = fresh_state()
        with self.assertRaises(RuntimeError):
            discord.deliver({"items": [story(i) for i in range(5)]}, state,
                "https://discord.com/api/webhooks/12345/valid_token", NOW,
                sender=lambda *_: (_ for _ in ()).throw(RuntimeError("refused")))
        self.assertEqual(state["posted"], {})
        self.assertEqual(state["history"], [])

    def test_host_moods_and_news_style(self):
        curious = story(1, topic="에이전트")
        skeptical = story(2, topic="성능 비교")
        serious = story(3, topic="보안")
        self.assertEqual(discord.mood_for(curious), "curious")
        self.assertEqual(discord.mood_for(skeptical), "skeptical")
        self.assertEqual(discord.mood_for(serious), "serious")
        msg = discord.payload_for([curious, skeptical, serious], NOW)
        self.assertIn("SIGNAL DESK", msg["content"])
        self.assertEqual(len(msg["embeds"]), 1)
        self.assertEqual(msg["embeds"][0]["color"], discord.MOOD_COLORS["curious"])
        self.assertIn("내 평가", msg["embeds"][0]["description"])
        self.assertTrue(msg["embeds"][0]["url"].startswith("https://"))

    def test_gemini_editorial_voice_and_sensitive_override(self):
        item = story(10, topic="에이전트")
        item.update({"voiceMood": "skeptical",
                     "voiceHook": "흥, 주장만 그럴듯한 건 아니겠지? 직접 뜯어보자고.",
                     "voiceTake": "이번 발표에서는 비교 기준이 명확하지 않아. 그거부터 따져야겠어."})
        mood, hook, take = discord.host_lines(item)
        self.assertEqual(mood, "skeptical")
        self.assertIn("직접 뜯어보자고", hook)
        self.assertIn("비교 기준", take)
        item["topic"] = "보안"
        self.assertEqual(discord.host_lines(item)[0], "serious")

    def test_lore_is_rare_and_contextual(self):
        items = [story(i, topic="보안") for i in range(3)]
        state = fresh_state()
        self.assertIsNone(discord.lore_aside(items, state))
        state["totalPosted"] = 17
        chosen = discord.lore_aside(items, state)
        self.assertEqual(chosen[0], "hongwon")
        state["loreMentions"] = {"hongwonAt": 20}
        state["totalPosted"] = 22
        self.assertIsNone(discord.lore_aside(items, state))
        state["totalPosted"] = 35
        speech = story(23, topic="음성 생성")
        speech["title"] = "대화형 음성 AI 모델"
        chosen = discord.lore_aside([speech], state)
        self.assertEqual(chosen[0], "honglu")
        payload = discord.payload_for([speech], NOW, state)
        self.assertIn("홍루", payload["embeds"][0]["description"])
        self.assertNotIn("홍루", discord.payload_for([speech], NOW)["embeds"][0]["description"])

    def test_lore_not_inserted_into_unrelated_news(self):
        state = fresh_state()
        state["totalPosted"] = 200
        items = [story(1, topic="반도체"), story(2, topic="연구·논문")]
        self.assertIsNone(discord.lore_aside(items, state))

    def test_success_records_lore_frequency(self):
        state = fresh_state()
        state["totalPosted"] = 17
        items = [story(0, topic="보안"), story(1, topic="정책·규제"), story(2, topic="AI 기업")]
        sent = []
        result = discord.deliver({"items": items}, state,
            "https://discord.com/api/webhooks/12345/valid_token", NOW,
            sender=lambda url, payload: sent.append(payload) or "123456789012345678")
        self.assertEqual(result["status"], "sent")
        self.assertEqual(state["totalPosted"], 18)
        self.assertEqual(state["loreMentions"]["hongwonAt"], 18)
        self.assertEqual(len(state["posted"]), 1)
        self.assertEqual(len(sent[0]["embeds"]), 1)

    def test_one_story_is_always_detailed(self):
        items = [story(i) for i in range(5)]
        payload = discord.payload_for(items, NOW)
        self.assertEqual(len(payload["embeds"]), 1)
        self.assertEqual(sum("상세" in e["footer"]["text"] for e in payload["embeds"]), 1)
        self.assertEqual(sum("간략" in e["footer"]["text"] for e in payload["embeds"]), 0)
        self.assertIn("무슨 소란이지?", payload["embeds"][0]["description"])
        self.assertIn("실속은?", [x["name"] for x in payload["embeds"][0]["fields"]])
        self.assertIn("가장 중요한 소식 1건", payload["content"])

    def test_one_story_cap_applies_to_any_input_length(self):
        for size in (1, 2, 3, 4, 5):
            payload = discord.payload_for([story(i) for i in range(size)], NOW)
            self.assertEqual(len(payload["embeds"]), 1)
            self.assertEqual(sum("상세" in x["footer"]["text"]
                                 for x in payload["embeds"]), 1)
            self.assertEqual(sum("간략" in x["footer"]["text"]
                                 for x in payload["embeds"]), 0)

    def test_specific_comparison_only_shown_when_grounded(self):
        items = [story(i) for i in range(5)]
        items[0]["difference"] = "기존 기술 대비 구체적인 차이는 수집된 원문에서 미확인"
        payload = discord.payload_for(items, NOW)
        self.assertNotIn("뭐가 다른데?", [x["name"] for x in payload["embeds"][0]["fields"]])
        items[0]["difference"] = "공식 시험에서 기존 대비 대기 시간이 줄었다고 발표함."
        amended = discord.payload_for(items, NOW)
        self.assertIn("뭐가 다른데?", [x["name"] for x in amended["embeds"][0]["fields"]])

    def test_metadata_factual_and_opinion_are_visually_separate(self):
        items = [story(i) for i in range(3)]
        items[0].update({"sourceLabel": "공식 발표", "sourceType": "primary",
                         "cost": "원문 언급: 무료 공개", "performance": "미확인",
                         "why": "확인해야 하는 이유: 모델 배포 비용 절감",
                         "date": "2026-11-10"})
        payload = discord.payload_for(items, NOW)
        first = payload["embeds"][0]
        self.assertIn("2026-11-10 · 공식 발표 · 원문", first["footer"]["text"])
        self.assertIn("내 평가", first["description"])
        self.assertIn("무슨 소란이지?", first["description"])
        self.assertIn("무료 공개", str(first["fields"]))
        self.assertEqual(first["url"], "https://example.com/article/0")

    def test_discord_total_embed_limit_five_long_stories(self):
        items = [story(i) for i in range(5)]
        for item in items:
            item["title"] = "화제의 기술 " * 70
            for key in ("summary", "difference", "why", "usage", "cost", "performance",
                        "voiceHook", "voiceTake"):
                item[key] = "긴 설명 " * 300
            item["voiceMood"] = "curious"
        payload = discord.payload_for(items, NOW)
        self.assertLessEqual(sum(discord.embed_text_length(e) for e in payload["embeds"]), 6000)
        self.assertLessEqual(len(payload["embeds"]), 10)
        self.assertLessEqual(len(payload["content"]), 2000)
        self.assertTrue(all(len(e["fields"]) <= 25 for e in payload["embeds"]))

    def test_prioritizes_fresh_free_open_source_without_fabricated_claim(self):
        items = [story(1, "2026-11-07", score=70, topic="모델 출시"),
                 story(2, "2026-11-10", score=70, topic="GitHub 오픈소스"),
                 story(3, "2026-11-10", score=70, topic="에이전트"),
                 story(4, "2026-11-10", score=70, topic="코딩 도구")]
        items[1]["title"] = "무료 오픈소스 Windows 로컬 AI 도구"
        selected = discord.pick(items, fresh_state(), NOW.date())
        self.assertEqual(selected[0]["id"], "feed-2")
        self.assertEqual(len(selected), 1)

    def test_old_or_future_news_not_sent(self):
        items = [story(1, "2026-11-06"), story(2, "2026-11-11")]
        self.assertEqual(discord.pick(items, fresh_state(), NOW.date()), [])
        self.assertEqual(len(discord.pick(items + [story(3)], fresh_state(), NOW.date())), 1)

    def test_explicit_labeled_test_sends_without_touching_delivery_ledger(self):
        rows = [story(i) for i in range(5)]
        history = fresh_state()
        history["posted"] = {x["id"]: NOW.isoformat() for x in rows}
        history["history"] = [{"at": NOW.isoformat(), "count": 5}]
        before = json.dumps(history, sort_keys=True)
        outbound = []
        report = discord.send_labeled_test(
            {"items": rows}, "https://discord.com/api/webhooks/12345/valid_token", NOW,
            sender=lambda url, payload: outbound.append((url, payload)) or "123456789012345678")
        self.assertEqual(report, {"status": "test_sent", "count": 1})
        self.assertEqual(len(outbound), 1)
        self.assertIn("[테스트 전송", outbound[0][1]["content"])
        self.assertEqual(outbound[0][1]["username"], discord.WEBHOOK_USERNAME)
        self.assertEqual(sum("상세" in x["footer"]["text"] for x in outbound[0][1]["embeds"]), 1)
        self.assertEqual(sum("간략" in x["footer"]["text"] for x in outbound[0][1]["embeds"]), 0)
        self.assertEqual(outbound[0][1]["allowed_mentions"], {"parse": []})
        self.assertEqual(json.dumps(history, sort_keys=True), before)

    def test_test_send_does_not_send_without_eligible_news(self):
        sent = []
        report = discord.send_labeled_test(
            {"items": [story(1, "2026-11-01")]},
            "https://discord.com/api/webhooks/12345/valid_token",
            NOW, sender=lambda *args: sent.append(args))
        self.assertEqual(report["status"], "no_test_stories")
        self.assertEqual(sent, [])

    def test_test_send_rejects_unapproved_webhook(self):
        with self.assertRaises(ValueError):
            discord.send_labeled_test({"items": [story(i) for i in range(4)]},
                                      "https://discord.com.attacker.example/api/webhooks/5/token",
                                      NOW, sender=lambda *args: "123456789012345678")

    def test_cli_test_requires_explicit_authorization(self):
        with patch.dict(os.environ, {"SIGNAL_DESK_TEST_SEND": "", "DISCORD_WEBHOOK_URL": ""}):
            self.assertEqual(discord.main(["--test-send"]), 1)

    def test_headline_content_types_are_distinct_from_importance(self):
        cases = [
            ("오픈소스", {
                "title": "AI 에이전트용 새 모델 오픈소스 공개",
                "topic": "에이전트", "urls": ["https://tech.kakao.com/posts/example"],
            }),
            ("오픈소스", {
                "title": "SDK release", "topic": "GitHub 오픈소스",
                "urls": ["https://github.com/example/sdk/releases/tag/v1"],
            }),
            ("업데이트", {
                "title": "Copilot review overview updates", "topic": "코딩 도구",
                "urls": ["https://github.blog/changelog/copilot-update"],
            }),
            ("데이터셋", {
                "title": "Nemotron-Personas-Finance-Japan: A Synthetic Dataset",
                "topic": "미정", "urls": ["https://huggingface.co/blog/nvidia/example"],
            }),
            ("논문", {
                "title": "Efficient Transformer Study", "topic": "연구·논문",
                "urls": ["https://arxiv.org/abs/2611.00001"],
            }),
            ("연구", {
                "title": "Your Experts Have Partners",
                "summary": "HOPE is a pruning of experts research technique.",
                "topic": "미정", "urls": ["https://huggingface.co/blog/amazon/hope"],
            }),
            ("보안", {
                "title": "Partnering with experts for security", "topic": "보안",
                "urls": ["https://openai.com/index/security-update"],
            }),
            ("성능 비교", {
                "title": "새 AI 모델 벤치마크 결과", "topic": "성능 비교",
                "urls": ["https://www.aitimes.com/news/example"],
            }),
            ("뉴스", {
                "title": "AI 기업, 협력 관계 발표", "topic": "AI 기업",
                "urls": ["https://openai.com/index/partnership"],
            }),
        ]
        for expected, changes in cases:
            with self.subTest(expected=expected, title=changes["title"]):
                item = story(50)
                item.update(changes)
                self.assertEqual(discord.story_type(item), expected)
                self.assertTrue(discord.build_story_embed(item, detailed=True)["title"]
                                .startswith(discord.STORY_TYPE_ICON[expected] + " [" + expected + "] "))

    def test_github_news_is_not_automatically_open_source(self):
        item = story(51)
        item.update({"title": "GitHub Copilot changelog update",
                     "topic": "코딩 도구",
                     "urls": ["https://github.blog/changelog/copilot"],
                     "summary": "GitHub의 새로운 코드 리뷰 기능"})
        self.assertEqual(discord.story_type(item), "업데이트")
        item["title"] = "새로운 AI 도구 등장"
        item["summary"] = "GitHub에서 도구를 공개했다고 소개했지만 코드 링크는 없음"
        item["topic"] = "미정"
        self.assertEqual(discord.story_type(item), "뉴스")

    def test_content_type_in_single_featured_title(self):
        items = [story(i) for i in range(5)]
        items[0].update({"topic": "연구·논문",
                         "urls": ["https://arxiv.org/abs/2611.12345"]})
        items[2].update({"title": "Data set for voice agents", "topic": "미정"})
        payload = discord.payload_for(items, NOW)
        self.assertTrue(payload["embeds"][0]["title"].startswith("📄 [논문] "))
        self.assertEqual(len(payload["embeds"]), 1)

    def test_clean_editorial_style_has_no_emoji_clutter(self):
        items = [story(i) for i in range(5)]
        items[0].update({"topic": "연구·논문",
                         "urls": ["https://arxiv.org/abs/2611.12345"]})
        payload = discord.payload_for(items, NOW)
        self.assertTrue(payload["embeds"][0]["title"].startswith("📄 [논문] "))
        self.assertTrue(all("|" not in e["title"] for e in payload["embeds"]))
        self.assertTrue(all(not any(icon in e["description"] for icon in
                     ("🌸", "🌺", "⚔️", "🛠", "💰")) for e in payload["embeds"]))
        self.assertTrue(all(not any(icon in f["name"] for e in payload["embeds"]
                     for f in e["fields"] for icon in ("🌸", "🌺", "⚔️", "🛠", "💰"))
                     for _ in (0,)))
        self.assertEqual(sum("상세" in e["footer"]["text"] for e in payload["embeds"]), 1)
        self.assertEqual(sum("간략" in e["footer"]["text"] for e in payload["embeds"]), 0)

    def test_exactly_one_type_icon_in_each_headline_and_banner_flower(self):
        cases = [
            ("오픈소스", "🧩"), ("논문", "📄"), ("데이터셋", "🗂"),
            ("연구", "🔬"), ("AI 모델", "🤖"), ("업데이트", "🔄"),
            ("도구", "🛠"), ("무료 도구", "🛠"), ("보안", "🛡"),
            ("정책", "⚖️"), ("성능 비교", "📊"), ("뉴스", "📰"),
        ]
        self.assertEqual(discord.STORY_TYPE_ICON, dict(cases))
        items = [story(i) for i in range(5)]
        items[0].update({"title": "AI 도구 오픈소스 공개", "topic": "에이전트"})
        items[1].update({"title": "새 모델 연구 발표", "topic": "연구·논문",
                         "urls": ["https://arxiv.org/abs/2611.12345"]})
        items[2].update({"title": "새로운 합성 데이터셋", "topic": "미정"})
        items[3].update({"topic": "보안"})
        items[2].update({"title": "Copilot 기능 업데이트", "topic": "코딩 도구",
                         "urls": ["https://github.blog/changelog/copilot"]})
        payload = discord.payload_for(items, NOW)
        self.assertTrue(payload["content"].startswith("🌸 **SIGNAL DESK"))
        self.assertEqual(payload["content"].count("🌸"), 1)
        for item, embed in zip(items, payload["embeds"]):
            category = discord.story_type(item)
            icon = discord.STORY_TYPE_ICON[category]
            self.assertTrue(embed["title"].startswith(icon + " [" + category + "] "))
            self.assertEqual(sum(embed["title"].count(mark) for mark in
                                 set(discord.STORY_TYPE_ICON.values())), 1)
            self.assertFalse(any(mark in embed["description"]
                                 for mark in discord.STORY_TYPE_ICON.values()))
            self.assertFalse(any(mark in field["name"]
                                 for field in embed["fields"]
                                 for mark in discord.STORY_TYPE_ICON.values()))
        self.assertEqual(sum("상세" in e["footer"]["text"]
                             for e in payload["embeds"]), 1)
        self.assertEqual(sum("간략" in e["footer"]["text"]
                             for e in payload["embeds"]), 0)

    def test_official_model_launch_is_first_even_above_routine_updates(self):
        routine = story(1, score=96, topic="코딩 도구")
        routine["title"] = "New useful coding features for developers"
        carbon = story(2, score=88, topic="모델 출시")
        carbon.update({"title": "Gemini 4 Carbon is now available",
                       "sourceLabel": "Google DeepMind", "majorLaunch": True})
        second = story(3, score=82, topic="반도체")
        picked = discord.pick([routine, carbon, second], fresh_state(), NOW.date())
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["id"], carbon["id"])
        self.assertTrue(discord.payload_for(picked, NOW)["embeds"][0]["title"]
                        .startswith("🤖 [AI 모델] Gemini 4 Carbon"))

    def test_single_daily_card_can_include_one_official_picture(self):
        item = story(80)
        item["urls"] = ["https://deepmind.google/blog/model-release"]
        item["imageUrl"] = "https://deepmind.google/static/model-cover.jpg"
        payload = discord.payload_for([item, story(81)], NOW)
        self.assertEqual(len(payload["embeds"]), 1)
        self.assertEqual(payload["embeds"][0]["image"],
                         {"url": "https://deepmind.google/static/model-cover.jpg"})
        self.assertEqual(payload["embeds"][0]["url"], item["urls"][0])

    def test_unsafe_or_missing_picture_keeps_original_text_card(self):
        article = story(82)
        article["urls"] = ["https://openai.com/index/launch"]
        for url in ("", "http://openai.com/img.jpg",
                    "https://openai.com.attacker.example/bad.png",
                    "https://127.0.0.1/internal.png",
                    "https://openai.com/media/logo.svg"):
            with self.subTest(url=url):
                article["imageUrl"] = url
                e = discord.build_story_embed(article, detailed=True)
                self.assertNotIn("image", e)
                self.assertIn("무슨 소란이지?", e["description"])
        article["imageUrl"] = "https://images.ctfassets.net/official/photo.webp"
        embed = discord.build_story_embed(article, detailed=True)
        self.assertEqual(embed["image"]["url"], article["imageUrl"])

    def test_invalid_ledger_refuses_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                discord.load_state(path)


if __name__ == "__main__":
    unittest.main()
