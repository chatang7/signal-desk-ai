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
        self.assertEqual({i["id"] for i in selected}, {"feed-1", "feed-5", "feed-6"})

    def test_requires_minimum_three(self):
        self.assertEqual(discord.pick([story(1), story(2)], fresh_state(), NOW.date()), [])

    def test_excludes_previous_deliveries(self):
        s = fresh_state()
        s["posted"]["feed-1"] = NOW.isoformat()
        selected = discord.pick([story(i) for i in range(1, 6)], s, NOW.date())
        self.assertNotIn("feed-1", {i["id"] for i in selected})

    def test_caps_source_and_topic(self):
        items = [story(i, source="one", topic="same") for i in range(6)]
        self.assertEqual(discord.pick(items, fresh_state(), NOW.date()), [])

    def test_cooldown_and_daily_limit(self):
        s = fresh_state()
        self.assertTrue(discord.can_send(s, NOW))
        s["history"].append({"at": (NOW-dt.timedelta(hours=2)).isoformat(), "count": 3})
        self.assertFalse(discord.can_send(s, NOW))
        evening = NOW.replace(hour=23)
        s["history"] = [
            {"at": (evening-dt.timedelta(hours=18)).isoformat(), "count": 3},
            {"at": (evening-dt.timedelta(hours=9)).isoformat(), "count": 3},
        ]
        self.assertFalse(discord.can_send(s, evening))

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
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertLessEqual(len(payload["content"]), 2000)
        self.assertEqual(len(payload["embeds"]), 5)
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
        self.assertEqual(report["count"], 5)

    def test_success_records_and_repeat_skips(self):
        state = fresh_state()
        sent = []
        def fake_send(url, payload):
            sent.append(payload)
            return "123456789012345678"
        posts = [story(i) for i in range(5)]
        report = discord.deliver({"items": posts}, state,
            "https://discord.com/api/webhooks/12345/valid_token", NOW, sender=fake_send)
        self.assertEqual(report["status"], "sent")
        self.assertEqual(len(state["posted"]), 5)
        self.assertEqual(state["history"][0]["messageId"], "123456789012345678")
        later = NOW+dt.timedelta(hours=12)
        self.assertEqual(discord.deliver({"items": posts}, state, "", later,
                                         dry_run=True)["status"], "no_new_stories")
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
        self.assertIn("🌸", msg["content"])
        self.assertEqual(len({x["color"] for x in msg["embeds"]}), 3)
        self.assertTrue(all("내 평가" in x["description"] or "한마디" in x["description"]
                            for x in msg["embeds"]))
        self.assertTrue(all(x["url"].startswith("https://") for x in msg["embeds"]))

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
        self.assertEqual(state["totalPosted"], 20)
        self.assertEqual(state["loreMentions"]["hongwonAt"], 20)
        self.assertEqual(len(state["posted"]), 3)
        self.assertEqual(len(sent), 1)

    def test_five_story_hybrid_is_two_featured_three_medium(self):
        items = [story(i) for i in range(5)]
        payload = discord.payload_for(items, NOW)
        self.assertEqual(len(payload["embeds"]), 5)
        self.assertEqual(sum("주목할 소식" in e["title"] for e in payload["embeds"]), 2)
        self.assertEqual(sum("제법 쓸 만한 물건" in e["title"] for e in payload["embeds"]), 3)
        self.assertIn("🌸 무슨 소란이지?", payload["embeds"][0]["description"])
        self.assertIn("💰 실속은?", [x["name"] for x in payload["embeds"][0]["fields"]])
        self.assertIn("🌸 핵심", payload["embeds"][4]["description"])
        self.assertIn("🛠 쓸모는 있겠지?", [x["name"] for x in payload["embeds"][4]["fields"]])
        self.assertIn("2건은 자세히", payload["content"])

    def test_four_and_three_story_hybrid_keep_one_featured(self):
        for size in (3, 4):
            payload = discord.payload_for([story(i) for i in range(size)], NOW)
            self.assertEqual(sum("주목할 소식" in x["title"] for x in payload["embeds"]), 1)
            self.assertEqual(sum("제법 쓸 만한 물건" in x["title"] for x in payload["embeds"]), size - 1)

    def test_specific_comparison_only_shown_when_grounded(self):
        items = [story(i) for i in range(5)]
        items[0]["difference"] = "기존 기술 대비 구체적인 차이는 수집된 원문에서 미확인"
        items[1]["difference"] = "기존 대비 공식 시험에서 대기 시간이 줄었다고 발표함."
        payload = discord.payload_for(items, NOW)
        self.assertNotIn("⚔️ 뭐가 다른데?", [x["name"] for x in payload["embeds"][0]["fields"]])
        self.assertIn("⚔️ 뭐가 다른데?", [x["name"] for x in payload["embeds"][1]["fields"]])

    def test_metadata_factual_and_opinion_are_visually_separate(self):
        items = [story(i) for i in range(3)]
        items[0].update({"sourceLabel": "공식 발표", "sourceType": "primary",
                         "cost": "원문 언급: 무료 공개", "performance": "미확인",
                         "why": "확인해야 하는 이유: 모델 배포 비용 절감",
                         "date": "2026-11-10"})
        items[1]["sourceType"] = "secondary"
        payload = discord.payload_for(items, NOW)
        first = payload["embeds"][0]
        self.assertIn("2026-11-10 · 공식 발표 · 원문", first["footer"]["text"])
        self.assertIn("내 평가", first["description"])
        self.assertIn("무슨 소란이지?", first["description"])
        self.assertIn("무료 공개", str(first["fields"]))
        self.assertIn("보도", payload["embeds"][1]["footer"]["text"])
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
        self.assertEqual(len(selected), 4)

    def test_old_or_future_news_not_sent(self):
        items = [story(1, "2026-11-06"), story(2, "2026-11-11"),
                 story(3, "2026-11-10"), story(4, "2026-11-10")]
        self.assertEqual(discord.pick(items, fresh_state(), NOW.date()), [])

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
        self.assertEqual(report, {"status": "test_sent", "count": 5})
        self.assertEqual(len(outbound), 1)
        self.assertIn("[테스트 전송", outbound[0][1]["content"])
        self.assertEqual(outbound[0][1]["username"], "SIGNAL DESK TEST")
        self.assertEqual(sum("주목할 소식" in x["title"] for x in outbound[0][1]["embeds"]), 2)
        self.assertEqual(sum("제법 쓸 만한 물건" in x["title"] for x in outbound[0][1]["embeds"]), 3)
        self.assertEqual(outbound[0][1]["allowed_mentions"], {"parse": []})
        self.assertEqual(json.dumps(history, sort_keys=True), before)

    def test_test_send_does_not_send_when_insufficient_news(self):
        sent = []
        report = discord.send_labeled_test(
            {"items": [story(1)]}, "https://discord.com/api/webhooks/12345/valid_token",
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

    def test_invalid_ledger_refuses_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                discord.load_state(path)


if __name__ == "__main__":
    unittest.main()
