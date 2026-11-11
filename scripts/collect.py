#!/usr/bin/env python3
"""SIGNAL DESK zero-key public AI news collector (Python standard library only)."""
from __future__ import annotations

import datetime as dt
import difflib
import unicodedata
import email.utils
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "news.json"
NOW = dt.datetime.now(dt.timezone.utc)
MAX_AGE_DAYS = 35  # Keep a month of history; not a 90-day backlog.
NEW_AGE_DAYS = 7  # Incoming feed items must be published within the last week.
MAX_ITEMS = 500
HEADERS = {"User-Agent": "SignalDeskNewsCollector/3.0 (+GitHub-Actions; public-feed-reader)", "Accept": "application/json, application/xml, application/rss+xml, text/xml, */*"}
AI_TERMS = re.compile(r"\b(ai|llm|gpt|language model|foundation model|transformer|diffusion|agent|mcp|chatbot|openai|gemini|claude|deepseek|generative|reasoning|inference|prompt|machine learning|neural|cuda|gpu|npu|robotics|multimodal|tokeniz|retrieval|rag|computer vision|speech synth|text.to.image|text.to.video)\b|인공지능|생성형|언어\s?모델|머신러닝|에이전트|딥러닝|모델\s?학습|추론|멀티모달|챗봇|음성\s?생성|영상\s?생성|챗\s?지피티|챗gpt|클로드|제미나이|코덱스|딥시크|엔비디아|오픈소스", re.I)

REPOS = [
    ("openai/codex", "Codex", "코딩 도구"),
    ("google-gemini/gemini-cli", "Gemini CLI", "코딩 도구"),
    ("anthropics/claude-code", "Claude Code", "코딩 도구"),
    ("huggingface/transformers", "Transformers", "GitHub 오픈소스"),
    ("vllm-project/vllm", "vLLM", "AI 인프라"),
    ("ollama/ollama", "Ollama", "무료 도구"),
    ("comfyanonymous/ComfyUI", "ComfyUI", "이미지 생성"),
    ("ggml-org/llama.cpp", "llama.cpp", "무료 도구"),
]
RSS = [
    ("OpenAI 공식 발표", "https://openai.com/news/rss.xml", "해외"),
    ("Google AI 블로그", "https://blog.google/technology/ai/rss/", "해외"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", "해외"),
    ("카카오테크", "https://tech.kakao.com/feed/", "한국"),
    ("네이버 D2", "https://d2.naver.com/d2.atom", "한국"),
    ("Google Research", "https://research.google/blog/rss/", "해외"),
    ("Hugging Face 블로그", "https://huggingface.co/blog/feed.xml", "해외"),
    ("Mistral AI", "https://mistral.ai/news/rss", "해외"),
    ("NVIDIA 개발자", "https://developer.nvidia.com/blog/feed/", "해외"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/blog/feed/", "해외"),
    ("GitHub Copilot", "https://github.blog/changelog/label/copilot/feed/", "해외"),
    ("과학기술정보통신부", "https://www.msit.go.kr/user/rss/rss.do?bbsSeqNo=94", "한국"),
    ("AI타임스 (보도)", "https://www.aitimes.com/rss/allArticle.xml", "한국"),
]
FOCUSED_SOURCES = {"OpenAI 공식 발표", "Google AI 블로그", "Google DeepMind", "Google Research", "Hugging Face 블로그", "Mistral AI", "GitHub Copilot"}
SECONDARY_SOURCES = {"AI타임스 (보도)"}
# Official alternate endpoint for transient RSS/XML errors on DeepMind's main host.
RSS_ALTERNATES = {"Google DeepMind": "https://deepmind.com/blog/feed/basic/"}



def strip_markup(raw: str, size: int = 500) -> str:
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()[:size]


def canonical_url(url: str) -> str:
    try:
        u = urllib.parse.urlsplit((url or "").strip())
        if u.scheme.lower() not in {"http", "https"} or not u.netloc or u.username or u.password:
            return ""
        clean = u.path.rstrip("/") or "/"
        params = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
                  if not (k.lower().startswith("utm_") or k.lower() in {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"})]
        query = urllib.parse.urlencode(sorted(params))
        return urllib.parse.urlunsplit((u.scheme.lower(), u.netloc.lower(), clean, query, ""))
    except ValueError:
        return ""


def date_str(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        d = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        try:
            d = email.utils.parsedate_to_datetime(str(raw))
        except (TypeError, ValueError):
            return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).date().isoformat()


def in_window(date: str, max_age_days: int = MAX_AGE_DAYS) -> bool:
    try:
        age = (NOW.date() - dt.date.fromisoformat(date)).days
        return -1 <= age <= max_age_days
    except ValueError:
        return False


def classify(text: str, fallback: str = "미정") -> str:
    s = text.lower()
    checks = [
        (r"cve[- ]|vulnerab|security|exploit|보안|취약점|공격|악성", "보안"),
        (r"policy|regulat|law|법안|규제|정책|법률", "정책·규제"),
        (r"gpu|nvidia|cuda|chip|semiconductor|hbm|반도체|엔비디아|칩셋", "반도체"),
        (r"datacenter|data center|infrastructure|serving|inference engine|클러스터|서빙|인프라", "AI 인프라"),
        (r"image generat|text.to.image|diffusion|flux\b|이미지 생성|이미지 모델", "이미지 생성"),
        (r"video generat|text.to.video|영상 생성|비디오 생성", "영상 생성"),
        (r"speech|voice|audio|tts|음성 생성|음성 인식|오디오", "음성 생성"),
        # Explicit code release is stronger evidence than incidental 'agent' or 'code'.
        (r"open.source|\boss\b|오픈소스|소스 공개", "GitHub 오픈소스"),
        (r"agent|mcp|tool use|에이전트|자동화", "에이전트"),
        (r"code|coding|codex|copilot|developer tool|코딩|개발 도구|cli\b", "코딩 도구"),
        (r"benchmark|eval|leaderboard|성능 비교|벤치마크", "성능 비교"),
        (r"paper|research|논문|연구", "연구·논문"),
        (r"model|gpt|gemini|claude|llm|언어 모델|모델 출시", "모델 출시"),
        (r"funding|investment|acquisition|투자|인수|기업", "AI 기업"),
    ]
    for p, category in checks:
        if re.search(p, s, re.I):
            return category
    return fallback


def create_item(title: str, link: str, date: str, summary: str, source: str, region: str, topic: str = "미정", reliability: int = 3, external_id: str = "") -> dict | None:
    link = canonical_url(link)
    title = strip_markup(title, 260)
    if not link or not title or not in_window(date, NEW_AGE_DAYS):
        return None
    key = external_id or link
    sid = hashlib.sha256(key.encode()).hexdigest()[:22]
    return {
        "id": "feed-" + sid,
        "externalId": key,
        "title": title,
        "topic": topic,
        "region": region,
        "date": date,
        "summary": strip_markup(summary),
        "difference": "",
        "why": "",
        "usage": "",
        "cost": "",
        "performance": "",
        "urls": [link],
        "importance": 3,
        "reliability": reliability,
        "pricing": "미정",
        "status": "오늘 읽을 것" if (NOW.date() - dt.date.fromisoformat(date)).days <= 2 else "나중에 검토",
        "notes": "자동 수집 메타데이터. 공식 정보와 주장에 대한 독립 검증은 별도 필요.",
        "selected": False,
        "groupKey": "",
        "importedFrom": "public-api",
        "sourceLabel": source,
        "sourceCheckedAt": NOW.isoformat(),
    }


def fetch(url: str, max_bytes: int = 2_500_000) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=18) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"응답 용량 {max_bytes / 1_000_000:.1f}MB 초과")
                return data
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last = e
            if attempt == 0:
                time.sleep(.5)
    raise RuntimeError(str(last))


def collect_releases(repo: str, label: str, topic: str) -> list:
    # The Codex release body can exceed the default response cap.
    if repo == "openai/codex":
        data = [json.loads(fetch("https://api.github.com/repos/openai/codex/releases/latest", max_bytes=12_000_000))]
    else:
        u = "https://api.github.com/repos/" + repo + "/releases?per_page=8"
        data = json.loads(fetch(u))
    if not isinstance(data, list):
        raise ValueError("GitHub releases 배열 아님")
    out = []
    for rel in data:
        if rel.get("draft") or rel.get("prerelease") or not rel.get("published_at"):
            continue
        day = date_str(rel["published_at"])
        label_name = rel.get("name") or rel.get("tag_name") or "release"
        item = create_item(
            f"{label} · {label_name}", rel.get("html_url", ""), day,
            rel.get("body", "") or "공식 릴리스 공개. 자세한 변경 사항은 릴리스 노트 참고.",
            "GitHub · " + label, "해외", topic, 4,
            f"github:{repo}:{rel.get('id','')}"
        )
        if item:
            out.append(item)
        if len(out) >= 3:
            break
    return out


def xml_value(elem: ET.Element, tag_names: list[str]) -> str:
    for name in tag_names:
        node = elem.find(name)
        if node is not None:
            if node.text and node.text.strip():
                return node.text.strip()
            if name.endswith('link') or name == 'link':
                href = node.attrib.get("href")
                if href:
                    return href
    return ""


def collect_rss(name: str, url: str, region: str) -> list:
    xml = fetch(url)
    # Some RSS providers have stray invalid XML control characters.
    xml = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", xml)
    root = ET.fromstring(xml)
    is_atom = root.tag.endswith("feed")
    rows = root.findall("{http://www.w3.org/2005/Atom}entry") if is_atom else root.findall("./channel/item")
    if not rows:
        raise ValueError("RSS/Atom 항목 없음")
    result = []
    for el in rows[:65]:
        if is_atom:
            title = xml_value(el, ["{http://www.w3.org/2005/Atom}title"])
            link = xml_value(el, ["{http://www.w3.org/2005/Atom}link"])
            when = xml_value(el, ["{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"])
            desc = xml_value(el, ["{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content"])
        else:
            title = xml_value(el, ["title"])
            link = xml_value(el, ["link"])
            when = xml_value(el, ["pubDate", "{http://purl.org/dc/elements/1.1/}date"])
            desc = xml_value(el, ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"])
        # Broad company/policy feeds must mention an AI-specific term in the title.
        # Do not import generic programming, event or corporate news just because a footer says AI.
        if name not in FOCUSED_SOURCES and not AI_TERMS.search(title):
            continue
        day = date_str(when)
        item = create_item(title, link, day, desc, name, region, classify(title, "미정"),
                           2 if name in SECONDARY_SOURCES else 4)
        if item and name in SECONDARY_SOURCES:
            item["notes"] = "언론 보도 원문. 사실 및 수치의 공식 1차 출처 확인 필요."
            item["sourceType"] = "secondary"
        if item:
            result.append(item)
        if len(result) >= (6 if name in SECONDARY_SOURCES else 10):
            break
    return result


def collect_rss_with_fallback(name: str, url: str, region: str) -> list:
    try:
        return collect_rss(name, url, region)
    except (ET.ParseError, RuntimeError, urllib.error.URLError, ValueError):
        alternate = RSS_ALTERNATES.get(name)
        if not alternate:
            raise
        return collect_rss(name, alternate, region)


def collect_papers() -> list:
    data = json.loads(fetch("https://huggingface.co/api/daily_papers?limit=25"))
    if not isinstance(data, list):
        raise ValueError("Hugging Face papers 배열 아님")
    result = []
    for record in data[:25]:
        p = record.get("paper", record)
        pid = str(p.get("id") or p.get("paperId") or "")
        if not re.fullmatch(r"\d{4}\.\d{4,6}(?:v\d+)?", pid):
            continue
        day = date_str(p.get("publishedAt") or record.get("publishedAt"))
        item = create_item(p.get("title", ""), "https://arxiv.org/abs/" + pid,
                           day, p.get("summary") or p.get("abstract") or "초록 확인 필요.",
                           "Hugging Face Daily Papers", "해외", "연구·논문", 3,
                           "arxiv:" + re.sub(r"v\d+$", "", pid))
        if item:
            item["notes"] = "학술 초록/논문 메타데이터입니다. 동료평가·재현 검증은 미확인."
            result.append(item)
        if len(result) >= 12:
            break
    return result


def load_previous() -> tuple[list, dict]:
    if not OUTPUT.exists():
        return [], {}
    try:
        d = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d["items"], d
    except (OSError, ValueError):
        pass
    return [], {}


# A conservative rules-only duplicate detector. It is not semantic AI clustering.
_TITLE_STOP = {"the", "a", "an", "and", "for", "with", "in", "on", "of", "to", "by",
               "official", "update", "new", "latest", "release", "발표", "소식", "공개", "업데이트", "새로운"}


def title_tokens(title: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", title or "").casefold()
    return [t for t in re.findall(r"[a-z0-9가-힣]+(?:[.][0-9]+)*", normalized)
            if len(t) > 1 and t not in _TITLE_STOP]


def near_duplicate(a: dict, b: dict) -> bool:
    """Group near-identical announcements only, never differing version numbers."""
    try:
        if abs((dt.date.fromisoformat(a["date"]) - dt.date.fromisoformat(b["date"])).days) > 3:
            return False
    except (ValueError, KeyError, TypeError):
        return False
    ta, tb = title_tokens(str(a.get("title", ""))), title_tokens(str(b.get("title", "")))
    if len(ta) < 3 or len(tb) < 3:
        return False
    # A near-match with different versions or benchmark numbers isn't the same event.
    nums_a = set(re.findall(r"\d+(?:\.\d+)+|\b\d+\b", " ".join(ta)))
    nums_b = set(re.findall(r"\d+(?:\.\d+)+|\b\d+\b", " ".join(tb)))
    if nums_a != nums_b:
        return False
    sa, sb = set(ta), set(tb)
    overlap = len(sa & sb)
    jac = overlap / len(sa | sb)
    ratio = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    return overlap >= 3 and (jac >= .82 or (jac >= .68 and ratio >= .92))


def dedup_merge(old: list, new: list) -> list:
    # First preserve canonical IDs for exact URL / article ID matches.
    by_id = {}
    for raw in old + new:
        if not isinstance(raw, dict) or not raw.get("title") or not raw.get("urls"):
            continue
        url = canonical_url(raw["urls"][0])
        key = raw.get("externalId") or url
        if not url or not key or not in_window(str(raw.get("date", ""))):
            continue
        item = dict(raw)
        if key in by_id:
            # Keep earlier cross-source links and references across fresh API polls.
            prior = by_id[key]
            links = []
            for u in item.get("urls", []) + prior.get("urls", []):
                c = canonical_url(u)
                if c and c not in links and len(links) < 12:
                    links.append(c)
            item["urls"] = links
            sources = []
            for label in [item.get("sourceLabel"), *prior.get("relatedSources", []),
                          prior.get("sourceLabel"), *item.get("relatedSources", [])]:
                if label and label not in sources and len(sources) < 12:
                    sources.append(label)
            item["relatedSources"] = sources
            item["duplicateCount"] = max(len(sources), int(prior.get("duplicateCount") or 1))
        by_id[key] = item
    ranked = sorted(by_id.values(), key=lambda x: (
        # Prefer authoritative official sources to third-party reporting when both match.
        x.get("sourceType") != "secondary", x.get("date") or "", x.get("id") or ""), reverse=True)
    filtered = []
    url_map = {}
    for item in ranked:
        url = canonical_url(item["urls"][0])
        canonical = url_map.get(url)
        if canonical is None:
            canonical = next((x for x in filtered if near_duplicate(x, item)), None)
        if canonical is not None:
            # Include links from secondary coverage without discarding the primary story.
            urls = canonical.setdefault("urls", [])
            for u in item.get("urls", []):
                c = canonical_url(u)
                if c and c not in {canonical_url(q) for q in urls} and len(urls) < 12:
                    urls.append(c)
                    url_map[c] = canonical
            sources = canonical.setdefault("relatedSources", [])
            if not sources and canonical.get("sourceLabel"):
                sources.append(canonical["sourceLabel"])
            for src in [item.get("sourceLabel"), *item.get("relatedSources", [])]:
                if src and src not in sources and len(sources) < 12:
                    sources.append(src)
            canonical["duplicateCount"] = max(len(sources), int(canonical.get("duplicateCount") or 1))
            continue
        item.setdefault("relatedSources", [item.get("sourceLabel", "")])
        filtered.append(item)
        for link in item.get("urls", []):
            key_link = canonical_url(link)
            if key_link:
                url_map[key_link] = item
    # Show newest event first; keep no more than a month of records in the server feed.
    return sorted(filtered, key=lambda x: (x.get("date") or "", x.get("id") or ""), reverse=True)[:MAX_ITEMS]


def main() -> int:
    old, prior = load_previous()
    incoming = []
    sources = []
    for repo, label, topic in REPOS:
        try:
            rows = collect_releases(repo, label, topic)
            incoming.extend(rows)
            sources.append({"source": label + " 릴리스", "ok": True, "count": len(rows)})
        except Exception as e:
            sources.append({"source": label + " 릴리스", "ok": False, "error": str(e)[:180]})
    for name, url, region in RSS:
        try:
            rows = collect_rss_with_fallback(name, url, region)
            incoming.extend(rows)
            sources.append({"source": name, "ok": True, "count": len(rows)})
        except Exception as e:
            sources.append({"source": name, "ok": False, "error": str(e)[:180]})
    try:
        rows = collect_papers()
        incoming.extend(rows)
        sources.append({"source": "Hugging Face 논문", "ok": True, "count": len(rows)})
    except Exception as e:
        sources.append({"source": "Hugging Face 논문", "ok": False, "error": str(e)[:180]})

    # Never wipe all previously collected items on an all-source failure.
    merged = dedup_merge(old, incoming)
    report = {
        "schema": "signal-desk-public-feed-v1",
        "generatedAt": NOW.isoformat(),
        "successfulSources": sum(s["ok"] for s in sources),
        "failedSources": sum(not s["ok"] for s in sources),
        "sources": sources,
        "items": merged,
        "quality": {"freshIntakeDays": NEW_AGE_DAYS, "archiveDays": MAX_AGE_DAYS,
                    "nearDuplicateRule": "strict-title-and-number", "incoming": len(incoming),
                    "unique": len(merged), "previous": len(old)},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"수집 {len(incoming)}건 · 보관 {len(merged)}건 · 소스 성공 {report['successfulSources']}/{len(sources)}")
    for source in sources:
        if not source["ok"]:
            print("[경고]", source["source"], source["error"])
    return 0 if report["successfulSources"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())