#!/usr/bin/env python3
"""Send a concise, evidence-labelled SIGNAL DESK briefing to one Discord webhook.

Runs only for schedules or explicit workflow dispatch opt-in (enforced by Actions).
No secret URL, response body, or webhook token is ever logged or saved.
"""
from __future__ import annotations

import datetime as dt
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


def payload_for(items: list, when: dt.datetime) -> dict:
    embeds = []
    for item in items[:MAX_STORIES]:
        link = next((u for u in item["urls"] if safe_link(u)), "")
        source = safe_text(item.get("sourceLabel", "출처 미상"), 70)
        label = "Gemini 보조 분석" if item.get("mode") == "ai-assisted" else "원문 기반 정리"
        if item.get("sourceType") == "secondary":
            label += " · 언론 보도"
        desc = safe_text(item.get("summary") or "세부 내용은 원문 확인", 315)
        difference = safe_text(item.get("difference"), 170)
        usage = safe_text(item.get("usage"), 170)
        fields = [{"name": "실무 확인", "value": usage or "원문에서 확인 필요", "inline": False}]
        if difference and not difference.startswith("기존 기술 대비 구체적인 차이는"):
            fields.insert(0, {"name": "기존 대비 변화", "value": difference, "inline": False})
        embeds.append({
            "title": safe_text(item.get("title"), 230),
            "url": link,
            "description": desc,
            "fields": fields,
            "footer": {"text": safe_text(source + " | " + label + " · 독립 검증 필요", 130)},
        })

    date = when.strftime("%Y-%m-%d %H:%M KST")
    return {
        "username": "SIGNAL DESK",
        "content": "📰 **AI 핵심 브리핑** | " + date + "\n중요한 소식 " + str(len(embeds))
                   + "건만 전송합니다. 성능·가격·주장의 독립 검증은 별도입니다.",
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
    payload = payload_for(selected, when)
    if dry_run:
        return {"status": "preview", "count": len(selected), "titles": [x["title"] for x in selected]}
    endpoint = webhook_endpoint(secret)
    message_id = sender(endpoint, payload)
    at = when.isoformat()
    state["posted"].update({item_key(item): at for item in selected})
    state["history"].append({"at": at, "count": len(selected), "messageId": message_id})
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
