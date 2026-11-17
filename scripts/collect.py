#!/usr/bin/env python3
"""水産・漁業ニュース収集スクリプト（岩手県・三陸重視）

使い方:
    python scripts/collect.py                 # 通常実行（ネットから取得）
    python scripts/collect.py --offline DIR   # DIR 内の *.xml を取得結果とみなして実行（テスト用）

出力:
    docs/data/news.json                 … ページが読み込む最新データ（保存期間分の蓄積）
    docs/data/archive/YYYY-MM-DD.json   … その日の収集結果（バックアップ）
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha1
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "docs" / "data"
JST = timezone(timedelta(hours=9))

RETENTION_DAYS = 30     # news.json に残す日数
MAX_ARTICLE_AGE = 14    # これより古い記事は取り込まない（日）
USER_AGENT = "Mozilla/5.0 (compatible; FisheriesNewsCollector/1.0)"

# 媒体名の表記ゆれ・ドメインを統一
SOURCE_ALIASES = {
    "iwate-np.co.jp": "岩手日報",
    "岩手日報": "岩手日報",
    "岩手日報社": "岩手日報",
    "asahi.com": "朝日新聞",
    "朝日新聞デジタル": "朝日新聞",
    "朝日新聞": "朝日新聞",
    "news.yahoo.co.jp": "Yahoo!ニュース",
    "Yahoo!ニュース": "Yahoo!ニュース",
    "suisan.co.jp": "水産経済新聞",
    "日刊水産経済新聞": "水産経済新聞",
    "水産経済新聞": "水産経済新聞",
    "minato-yamaguchi.co.jp": "みなと新聞",
    "みなと新聞": "みなと新聞",
    "nhk.or.jp": "NHK",
    "NHKニュース": "NHK",
    "khb-tv.co.jp": "KHB東日本放送",
    "kahoku.news": "河北新報",
    "河北新報オンライン": "河北新報",
}
# 主要媒体（ページで「主要媒体」として強調）
MAJOR_SOURCES = {"岩手日報", "朝日新聞", "Yahoo!ニュース", "水産経済新聞", "みなと新聞"}
# 水産専門紙（filter: false の取得元では、これらの媒体の記事は水産キーワードが無くても残す）
FISHERY_PAPERS = {"水産経済新聞", "みなと新聞"}


# ------------------------------------------------------------------ 設定読込
def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Classifier:
    """キーワード辞書による分類・スコアリング"""

    def __init__(self, conf: dict):
        self.exclude = [re.compile(p) for p in conf.get("exclude", [])]
        self.core = [re.compile(p) for p in conf.get("fishery_core", [])]
        self.areas = [self._compile(a) for a in conf.get("areas", [])]
        self.categories = [self._compile(c) for c in conf.get("categories", [])]

    @staticmethod
    def _compile(d: dict) -> dict:
        return {
            "id": d["id"],
            "label": d["label"],
            "weight": d.get("weight", 1),
            "patterns": [(p, re.compile(p)) for p in d.get("patterns", [])],
        }

    def _clean(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        for rx in self.exclude:
            text = rx.sub("　", text)
        return text

    def classify(self, title: str, summary: str = "") -> dict:
        # タイトルは本文より重視（タイトルで当たったら1.5倍）
        t = self._clean(title)
        s = self._clean(summary)
        is_fishery = any(rx.search(t) or rx.search(s) for rx in self.core)

        score = 0.0
        areas, cats, hits = [], [], []
        for group, bucket in ((self.areas, areas), (self.categories, cats)):
            for g in group:
                matched = []
                in_title = False
                for raw, rx in g["patterns"]:
                    m = rx.search(t) or rx.search(s)
                    if m:
                        matched.append(m.group(0))
                        in_title = in_title or bool(rx.search(t))
                if matched:
                    bucket.append(g["id"])
                    hits.extend(matched)
                    # 1分野につき weight + 追加ヒット毎に少し加点（上限あり）
                    pts = g["weight"] + min(len(matched) - 1, 3) * 1
                    score += pts * (1.5 if in_title else 1.0)

        # 岩手×水産の組み合わせを最重視
        if "iwate" in areas and is_fishery:
            score += 10
        return {
            "is_fishery": is_fishery,
            "score": round(score, 1),
            "areas": areas,
            "categories": cats,
            "keywords": sorted(set(hits), key=hits.index)[:12],
        }


# ------------------------------------------------------------------ 取得
def fetch(url: str, retries: int = 3) -> bytes:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"取得失敗: {last}")


def google_news_url(query: str, gconf: dict) -> str:
    period = gconf.get("period")
    q = f"{query} when:{period}" if period else query
    params = {
        "q": q,
        "hl": gconf.get("hl", "ja"),
        "gl": gconf.get("gl", "JP"),
        "ceid": gconf.get("ceid", "JP:ja"),
    }
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


# ------------------------------------------------------------------ 解析
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(el: ET.Element, *names: str) -> str:
    for c in el:
        if _local(c.tag) in names:
            return (c.text or "").strip()
    return ""


def _child(el: ET.Element, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s: str) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=JST)
    return d.astimezone(JST)


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0 / RSS 1.0 (RDF) / Atom を共通形式に"""
    root = ET.fromstring(data)
    items = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        title = _child_text(el, "title")
        link = _child_text(el, "link")
        if not link:  # Atom
            ln = _child(el, "link")
            if ln is not None:
                link = ln.get("href", "")
        summary = _child_text(el, "description", "summary", "content")
        date = _child_text(el, "pubDate", "date", "published", "updated")
        src_el = _child(el, "source")
        source = ""
        source_url = ""
        if src_el is not None:
            source = (src_el.text or "").strip()
            source_url = src_el.get("url", "")
        items.append({
            "title": html.unescape(title),
            "url": link,
            "summary": strip_html(summary),
            "published": parse_date(date),
            "source": source,
            "source_url": source_url,
        })
    return items


def normalize_source(name: str, url: str = "") -> str:
    name = (name or "").strip()
    if name in SOURCE_ALIASES:
        return SOURCE_ALIASES[name]
    host = urllib.parse.urlparse(url).netloc.lower()
    for dom, alias in SOURCE_ALIASES.items():
        if "." in dom and host.endswith(dom):
            return alias
    return name or host or "不明"


def clean_title(title: str, source: str) -> str:
    """Google ニュースは『見出し - 媒体名』形式なので末尾の媒体名を外す"""
    t = title.strip()
    m = re.match(r"^(.*)\s+-\s+([^-]{1,40})$", t)
    if m:
        t = m.group(1).strip()
    return t


def title_key(title: str) -> str:
    t = unicodedata.normalize("NFKC", title).lower()
    t = re.sub(r"[【】\[\]（）()「」『』\s、。・,.!?！？:：\-—–|｜/]", "", t)
    return t


# ------------------------------------------------------------------ メイン
def collect(offline_dir: Path | None = None) -> dict:
    src_conf = load_yaml("sources.yaml")
    clf = Classifier(load_yaml("keywords.yaml"))
    gconf = src_conf.get("google_news", {}) or {}
    now = datetime.now(JST)
    oldest = now - timedelta(days=MAX_ARTICLE_AGE)

    collected: dict[str, dict] = {}
    statuses = []
    for i, src in enumerate(src_conf.get("sources", []) or []):
        name = src["name"]
        if src.get("type") == "google_news":
            url = google_news_url(src["query"], gconf)
        else:
            url = src["url"]
        t0 = time.time()
        try:
            if offline_dir:
                f = offline_dir / f"{i:02d}.xml"
                if not f.exists():
                    f = offline_dir / "default.xml"
                data = f.read_bytes()
            else:
                data = fetch(url)
            raw_items = parse_feed(data)
            status = {"name": name, "ok": True, "fetched": len(raw_items)}
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {name}: {e}", file=sys.stderr)
            statuses.append({"name": name, "ok": False, "error": str(e)[:200]})
            continue

        kept = 0
        for it in raw_items:
            if not it["title"] or not it["url"]:
                continue
            pub = it["published"]
            if pub and pub < oldest:
                continue
            source = normalize_source(it["source"], it["source_url"] or it["url"])
            title = clean_title(it["title"], source)
            summary = it["summary"]
            # Google ニュースの description は見出しの繰り返しなので捨てる
            if summary.startswith(title[:15]) or "news.google.com" in it["url"]:
                summary = ""
            info = clf.classify(title, summary)
            needs_filter = src.get("filter", True) or source not in FISHERY_PAPERS
            if needs_filter and not info["is_fishery"]:
                continue
            key = sha1(title_key(title).encode()).hexdigest()[:16]
            if key in collected:
                collected[key]["via"] = sorted(set(collected[key]["via"]) | {name})
                continue
            collected[key] = {
                "id": key,
                "title": title,
                "url": it["url"],
                "source": source,
                "major": source in MAJOR_SOURCES,
                "summary": summary[:200],
                "published": (pub or now).isoformat(timespec="minutes"),
                "via": [name],
                **{k: info[k] for k in ("score", "areas", "categories", "keywords")},
            }
            kept += 1
        status["kept"] = kept
        status["seconds"] = round(time.time() - t0, 1)
        statuses.append(status)
        if not offline_dir:
            time.sleep(1.0)  # 取得先への負荷軽減

    return {"now": now, "items": collected, "statuses": statuses}


def merge_with_history(result: dict) -> dict:
    now: datetime = result["now"]
    run_id = now.isoformat(timespec="minutes")
    path = DATA_DIR / "news.json"
    old_items = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            old_items = {a["id"]: a for a in old.get("articles", [])}
        except (json.JSONDecodeError, KeyError):
            old_items = {}

    merged = {}
    for k, a in result["items"].items():
        prev = old_items.get(k)
        a["first_seen"] = prev["first_seen"] if prev else run_id
        if prev:
            a["via"] = sorted(set(a["via"]) | set(prev.get("via", [])))
        merged[k] = a
    cutoff = now - timedelta(days=RETENTION_DAYS)
    for k, a in old_items.items():
        if k in merged:
            continue
        if datetime.fromisoformat(a["published"]) >= cutoff:
            merged[k] = a

    articles = sorted(merged.values(), key=lambda a: a["published"], reverse=True)
    new_count = sum(1 for a in articles if a["first_seen"] == run_id)
    return {
        "updated_at": run_id,
        "new_count": new_count,
        "total": len(articles),
        "sources": result["statuses"],
        "category_labels": category_labels(),
        "articles": articles,
    }


def category_labels() -> dict:
    conf = load_yaml("keywords.yaml")
    labels = {a["id"]: a["label"] for a in conf.get("areas", [])}
    labels.update({c["id"]: c["label"] for c in conf.get("categories", [])})
    return labels


def main() -> int:
    global DATA_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", type=Path, help="テスト用: RSSファイルのあるディレクトリ")
    ap.add_argument("--out", type=Path, default=DATA_DIR, help="出力先ディレクトリ")
    args = ap.parse_args()

    DATA_DIR = args.out
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    result = collect(args.offline)
    ok = [s for s in result["statuses"] if s["ok"]]
    if not ok:
        print("[ERROR] すべての取得元で失敗しました。データは更新しません。", file=sys.stderr)
        return 1
    out = merge_with_history(result)

    (DATA_DIR / "news.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    archive = DATA_DIR / "archive"
    archive.mkdir(exist_ok=True)
    today = result["now"].strftime("%Y-%m-%d")
    todays = [a for a in out["articles"] if a["first_seen"].startswith(today)]
    (archive / f"{today}.json").write_text(
        json.dumps({"date": today, "articles": todays}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"更新完了 {out['updated_at']}: 新着 {out['new_count']} 件 / 保持 {out['total']} 件")
    for s in result["statuses"]:
        mark = "OK " if s["ok"] else "NG "
        detail = f"{s.get('kept', 0)}/{s.get('fetched', 0)}件" if s["ok"] else s["error"]
        print(f"  {mark}{s['name']}: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
