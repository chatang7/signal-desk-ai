#!/usr/bin/env python3
"""Send a concise, evidence-labelled SIGNAL DESK briefing to one Discord webhook.

Runs only for schedules or explicit workflow dispatch opt-in (enforced by Actions).
No secret URL, response body, or webhook token is ever logged or saved.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "data" / "briefing.json"
STATE = ROOT / "data" / "discord_state.json"
KST = dt.timezone(dt.timedelta(hours=9))
MAX_STORIES = 5
MIN_STORIES = 3
MIN_SCORE = 62
MAX_NEWS_AGE_DAYS = 4
MIN_HOURS_BETWEEN_POSTS = 7
MAX_SENDS_PER_DAY = 2


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def safe_text(value, limit: int) -> str:
    clean = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(value or ""))).strip()
    clean = clean.replace("@everyone", "@ everyone").replace("@here", "@ here")
    return clean[:limit]


def safe_link(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url)
        if (p.scheme != "https" or not p.hostname or p.username or p.password
                or len(url) > 1000):
            return ""
        return url
    except (ValueError, TypeError):
        return ""


def item_key(item: dict) -> str:
    return str(item.get("id") or (item.get("urls") or [""])[0]).strip()[:180]


def fresh(item: dict, today: dt.date) -> bool:
    try:
        date = dt.date.fromisoformat(str(item.get("date", "")))
        age = (today - date).days
        return 0 <= age <= MAX_NEWS_AGE_DAYS
    except ValueError:
        return False


def load_state(path: Path = STATE) -> dict:
    if not path.exists():
        return {"schema": "signal-desk-discord-v1", "posted": {}, "history": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or payload.get("schema") != "signal-desk-discord-v1"
            or not isinstance(payload.get("posted"), dict) or not isinstance(payload.get("history"), list)):
        raise ValueError("Invalid Discord delivery ledger; refusing to send")
    return payload


def can_send(state: dict, now: dt.datetime) -> bool:
    history = state.get("history", [])
    today = now.date()
    daily = 0
    for record in history:
        try:
            moment = dt.datetime.fromisoformat(record["at"])
            if moment.tzinfo is None:
                return False  # Fail closed for unknown timestamps
            moment = moment.astimezone(KST)
            if moment.date() == today:
                daily += 1
            if abs((now - moment).total_seconds()) < MIN_HOURS_BETWEEN_POSTS * 3600:
                return False
        except (ValueError, KeyError, TypeError):
            return False
    return daily < MAX_SENDS_PER_DAY


def pick(items: list, state: dict, today: dt.date) -> list:
    posted = state.get("posted", {})
    candidates = []
    for item in items:
        if not isinstance(item, dict) or not item_key(item) or item_key(item) in posted:
            continue
        if not fresh(item, today) or int(item.get("score", 0) or 0) < MIN_SCORE:
            continue
        if not any(safe_link(url) for url in item.get("urls", [])[:3]):
            continue
        candidates.append(item)

    # Prefer verified source metadata and items Gemini actually analysed.
    candidates.sort(key=lambda x: (
        int(x.get("score", 0) or 0)
        + (8 if x.get("mode") == "ai-assisted" else 0)
        + (3 if x.get("sourceType") != "secondary" else -5),
        x.get("date", ""),
    ), reverse=True)
    selected, used_sources, used_topics, seen = [], {}, {}, set()
    for item in candidates:
        key = item_key(item)
        source = str(item.get("sourceLabel", ""))
        topic = str(item.get("topic", ""))
        if key in seen or used_sources.get(source, 0) >= 2 or used_topics.get(topic, 0) >= 2:
            continue
        selected.append(item)
        seen.add(key)
        used_sources[source] = used_sources.get(source, 0) + 1
        used_topics[topic] = used_topics.get(topic, 0) + 1
        if len(selected) >= MAX_STORIES:
            break
    return selected if len(selected) >= MIN_STORIES else []


# A distinctive fictional host voice, not a copy of original game dialogue.
OPENINGS = {
    "curious": ("흐응, 이건 제법 흥미로운데?", "이번 건 그냥 지나치긴 아깝겠어.",
                "뭐야, 이런 변화가 있었다고? 한번 들여다보자고.", "흥, 어디 얼마나 쓸 만한지 보자고."),
    "skeptical": ("발표는 화려하네. 그런데 근거는?", "감탄하기엔 일러. 먼저 따져봐야지.",
                  "자신감은 넘쳐 보이네. 실력도 그럴까?", "흥, 구호보다 중요한 건 실제 결과야."),
    "serious": ("……이번 일은 가볍게 넘길 수 없어.", "잠깐, 이런 문제에는 책임이 따르지.",
                "안전과 신뢰에 관한 일이야. 차분하게 보자고.", "여기서는 농담할 생각 없어."),
}
MOOD_COLORS = {"curious": 0xD080A2, "skeptical": 0xCD935A, "serious": 0x718CB7}


def mood_for(item: dict) -> str:
    if item.get("topic") in {"보안", "정책·규제"}:
        return "serious"
    specified = item.get("voiceMood")
    if isinstance(specified, str) and specified in OPENINGS:
        return specified
    if item.get("topic") == "성능 비교":
        return "skeptical"
    return "curious"


def host_lines(item: dict) -> tuple[str, str, str]:
    mood = mood_for(item)
    variant = int.from_bytes(hashlib.sha256(item_key(item).encode()).digest()[:4], "big") % len(OPENINGS[mood])
    hook = item.get("voiceHook")
    take = item.get("voiceTake")
    if (item.get("voiceMood") != mood or not isinstance(hook, str)
            or not 12 <= len(hook) <= 160 or re.search(r"홍루|홍원|가씨|가시춘", hook)):
        hook = OPENINGS[mood][variant]
    if (item.get("voiceMood") != mood or not isinstance(take, str)
            or not 18 <= len(take) <= 230 or re.search(r"홍루|홍원|가씨|가시춘", take)):
        why = str(item.get("why") or "").removeprefix("확인해야 하는 이유: ").strip()
        diff = str(item.get("difference") or "")
        if mood == "skeptical":
            take = ("그럴듯한 변화라는 건 인정하지. 하지만 " + diff[:140]
                    if diff and "미확인" not in diff else
                    "발표의 자신감과 실제 실력은 다른 문제야. 비교 조건을 따져봐야겠지.")
        elif mood == "serious":
            take = ("핵심은 바로 이거야. " + why[:140] if why and "발표의 구체적인" not in why
                    else "문제의 영향 범위부터 분명히 확인해야겠어.")
        else:
            take = ("내가 주목한 건 이 부분이야. " + why[:140] if why and "발표의 구체적인" not in why
                    else "흥미롭긴 하네. 그래도 실전에 통하는지는 따져볼 일이야.")
    return mood, safe_text(hook, 155), safe_text(take, 215)


def total_posted(state: dict) -> int:
    try:
        previous = int(state.get("totalPosted", 0) or 0)
        saved = sum(max(0, int(x.get("count", 0))) for x in state.get("history", []))
        return max(0, previous, saved)
    except (ValueError, TypeError, AttributeError):
        return 0


def lore_aside(items: list, state: dict | None) -> tuple[str, int, str] | None:
    """No lore on first few editions; only one relevant aside per ~18/36 articles."""
    if not state:
        return None
    total = total_posted(state)
    upcoming = total + len(items)
    previous = state.get("loreMentions") or {}
    if not isinstance(previous, dict):
        previous = {}
    if upcoming >= 36 and upcoming - int(previous.get("hongluAt", 0) or 0) >= 36:
        for index, item in enumerate(items):
            words = str(item.get("title") or "") + " " + str(item.get("summary") or "")
            if item.get("topic") in {"에이전트", "음성 생성"} and re.search(
                    r"대화|감정|사람|인간|음성|목소리|emotion|voice|conversation|social", words, re.I):
                return "honglu", index, ("홍루 오라버니라면 이런 대화를 어떻게 볼까… "
                                         "아니, 쓸데없는 생각이야. 뉴스나 마저 보자고.")
    if upcoming >= 18 and upcoming - int(previous.get("hongwonAt", 0) or 0) >= 18:
        for index, item in enumerate(items):
            if item.get("topic") in {"정책·규제", "보안", "AI 기업", "에이전트"}:
                return "hongwon", index, ("홍원을 맡은 입장에서 책임 문제를 가볍게 볼 순 없지. "
                                          "……흥, 다시 본론으로 돌아가자고.")
    return None


def payload_for(items: list, when: dt.datetime, state: dict | None = None) -> dict:
    lore = lore_aside(items[:MAX_STORIES], state)
    embeds = []
    for index, item in enumerate(items[:MAX_STORIES]):
        link = next((u for u in item["urls"] if safe_link(u)), "")
        source = safe_text(item.get("sourceLabel", "출처 미상"), 70)
        label = "Gemini 보조 분석" if item.get("mode") == "ai-assisted" else "원문 기반 정리"
        if item.get("sourceType") == "secondary":
            label += " · 언론 보도"
        mood, hook, take = host_lines(item)
        summary = safe_text(item.get("summary") or "자세한 내용은 원문에서 확인할 필요가 있어.", 315)
        description = "**" + hook + "**\n\n" + summary + "\n\n**🌸 내 생각**  " + take
        if lore and lore[1] == index:
            description += "\n\n*" + safe_text(lore[2], 160) + "*"
        difference = safe_text(item.get("difference"), 180)
        usage = safe_text(item.get("usage"), 175)
        fields = [{"name": "🛠 직접 따져볼 점", "value": usage or "실제 사용 조건을 확인해 봐야겠지.", "inline": False}]
        if difference and not difference.startswith("기존 기술 대비 구체적인 차이는"):
            fields.insert(0, {"name": "🔎 뭐가 달라졌나", "value": difference, "inline": False})
        embeds.append({
            "title": safe_text(item.get("title"), 230),
            "url": link,
            "description": description[:1100],
            "fields": fields,
            "color": MOOD_COLORS[mood],
            "footer": {"text": safe_text(source + " | " + label + " · 진행자의 의견과 보도 사실은 구분", 160)},
        })

    date = when.strftime("%Y-%m-%d %H:%M KST")
    moods = [mood_for(i) for i in items[:MAX_STORIES]]
    if moods.count("serious") >= 2:
        opening = "오늘은 가볍게 넘기면 안 될 이야기가 있네. 중요한 것부터 살펴보자고."
    elif moods.count("skeptical") >= 2:
        opening = "화려한 발표가 많네. 흥, 어디 실속도 그만한지 확인해 보자고."
    else:
        opening = "제법 흥미로운 물건도 보여. 실력은 어떤지 한번 살펴보자고."
    return {
        "username": "SIGNAL DESK",
        "content": ("🌸 **SIGNAL DESK | 오늘의 AI 소식**  " + date + "\n"
                    + str(len(embeds)) + "건을 골라봤어. " + opening + "\n"
                    "*가상 진행자의 감상과 기술 뉴스의 사실은 구분해서 읽어줘.*"),
        "embeds": embeds,
        "allowed_mentions": {"parse": []},
        "tts": False,
    }


def webhook_endpoint(secret: str) -> str:
    try:
        p = urllib.parse.urlsplit(secret.strip())
        if (p.scheme != "https" or p.hostname not in {"discord.com", "discordapp.com"}
                or p.username or p.password or p.query or p.fragment or
                not re.fullmatch(r"/api/webhooks/\d+/[A-Za-z0-9._-]+", p.path)):
            raise ValueError("Invalid webhook URL format")
        return urllib.parse.urlunsplit(("https", p.netloc, p.path, "wait=true", ""))
    except (ValueError, AttributeError):
        raise ValueError("Invalid webhook URL format") from None


def transmit(endpoint: str, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(2):
        req = urllib.request.Request(endpoint, data=body,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "SignalDeskDiscord/1.0"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=22) as resp:
                if resp.status != 200:
                    raise RuntimeError("Unexpected Discord response status")
                data = json.loads(resp.read(15_000))
            message_id = str(data.get("id", ""))
            if not re.fullmatch(r"\d{5,22}", message_id):
                raise RuntimeError("Discord message confirmation missing; ledger not updated")
            return message_id
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                try:
                    delay = float(exc.headers.get("Retry-After") or 0)
                except (ValueError, TypeError):
                    delay = 0
                if 0 < delay <= 20:
                    time.sleep(delay + 0.25)
                    continue
            # Never log full exception: urllib includes the secret webhook URL.
            raise RuntimeError("Discord rejected message (HTTP " + str(exc.code) + ")") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # A timeout can mean delivery succeeded. Do not retry ambiguous outcomes.
            raise RuntimeError("Discord delivery unconfirmed; not retrying automatically") from None
    raise RuntimeError("Discord rate limit; delivery skipped")


def deliver(briefing: dict, state: dict, secret: str, when: dt.datetime,
            dry_run: bool = False, sender=transmit) -> dict:
    if not can_send(state, when):
        return {"status": "cooldown", "count": 0}
    selected = pick(briefing.get("items", []), state, when.date())
    if not selected:
        return {"status": "no_new_stories", "count": 0}
    lore = lore_aside(selected, state)
    payload = payload_for(selected, when, state)
    if dry_run:
        return {"status": "preview", "count": len(selected), "titles": [x["title"] for x in selected]}
    endpoint = webhook_endpoint(secret)
    message_id = sender(endpoint, payload)
    prior_total = total_posted(state)
    at = when.isoformat()
    state["posted"].update({item_key(item): at for item in selected})
    state["history"].append({"at": at, "count": len(selected), "messageId": message_id})
    state["totalPosted"] = prior_total + len(selected)
    if lore:
        state.setdefault("loreMentions", {})[lore[0] + "At"] = state["totalPosted"]
    # Keep deduplication keys for at least several months; do not store secrets or text.
    state["history"] = state["history"][-90:]
    if len(state["posted"]) > 1500:
        state["posted"] = dict(list(state["posted"].items())[-1500:])
    state["updatedAt"] = at
    return {"status": "sent", "count": len(selected)}


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    dry_run = "--dry-run" in args
    secret = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not dry_run and not secret:
        print("Discord skip: DISCORD_WEBHOOK_URL GitHub secret not configured")
        return 0
    try:
        briefing = json.loads(FEED.read_text(encoding="utf-8"))
        if briefing.get("schema") != "signal-desk-briefing-v1":
            raise ValueError("Invalid briefing format")
        state = load_state()
        result = deliver(briefing, state, secret, now_kst(), dry_run=dry_run)
        if result["status"] == "sent":
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Discord status: " + result["status"] + " (" + str(result["count"]) + " stories)")
        return 0
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as e:
        # Whitelisted, sanitized error classes and messages only. Never print secret or HTTPError.
        print("Discord failure: " + (str(e) if isinstance(e, (ValueError, RuntimeError)) else type(e).__name__))
        return 1


if __name__ == "__main__":
    sys.exit(main())
