#!/usr/bin/env python3
"""収集済みニュース（docs/data/news.json）から Word 報告書（.docx）を作る

使い方:
    python scripts/report.py                # 日報：前回の報告以降〜今回（既定：直近1日）の新着
    python scripts/report.py --days 7       # 週報：直近7日分
    python scripts/report.py --days 30      # 月報：直近30日分

出力:
    docs/reports/水産ニュース_日報_YYYY-MM-DD.docx（週報・月報も同様）
    docs/reports/latest_daily.docx / latest_weekly.docx / latest_monthly.docx（最新版の固定名コピー）
    docs/reports/index.json                 （ページの「Word出力」一覧用）

環境変数 ANTHROPIC_API_KEY が設定されていれば、見出しをもとにした「AIによる概況まとめ」を冒頭に追加します（任意）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401  (将来の横向き対応用)
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.shared import Mm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data" / "news.json"
OUT_DIR = ROOT / "docs" / "reports"
JST = timezone(timedelta(hours=9))
FONT = "Yu Gothic"          # Word の既定和文フォント（游ゴシック）
ACCENT = RGBColor(0x0B, 0x5D, 0x8C)
HIGH = RGBColor(0xC2, 0x41, 0x0C)
MUTED = RGBColor(0x5D, 0x6B, 0x78)
HIGH_SCORE, MID_SCORE = 40, 22
KINDS = {1: ("日報", "daily"), 7: ("週報", "weekly"), 30: ("月報", "monthly")}


# ============================================================ データ整理
def load_keywords() -> dict:
    with open(ROOT / "config" / "keywords.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def core_title(t: str) -> str:
    """類似記事判定用：末尾の（放送局名）【地域】｜媒体などを除いて正規化"""
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"[（(][^（）()]{1,30}[）)]\s*$", "", t)
    t = re.sub(r"[｜|].*$", "", t)
    t = re.sub(r"【[^】]*】|\[[^\]]*\]", "", t)
    t = re.sub(r"[\s、。・,.!?！？「」『』“”\"'：:\-—–]", "", t)
    return t


def bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def cluster(articles: list[dict], threshold: float = 0.55) -> list[dict]:
    """同じ出来事を報じた記事（媒体違い・配信転載）を1つの話題にまとめる"""
    topics: list[dict] = []
    for a in sorted(articles, key=lambda x: (-x["score"], x["published"])):
        bg = bigrams(core_title(a["title"]))
        best, best_sim = None, 0.0
        for t in topics:
            inter = len(bg & t["_bg"])
            sim = inter / min(len(bg), len(t["_bg"]))  # 短い方基準（見出しの長短差に強く）
            if sim > best_sim:
                best, best_sim = t, sim
        if best and best_sim >= threshold:
            best["others"].append(a)
            continue
        topics.append({"main": a, "others": [], "_bg": bg})
    for t in topics:
        t["all"] = [t["main"], *t["others"]]
        # 代表記事は主要媒体を優先（岩手日報 > 朝日 > 水産経済 > …）
        pri = {"岩手日報": 0, "朝日新聞": 1, "水産経済新聞": 2, "みなと新聞": 3, "Yahoo!ニュース": 4}
        t["all"].sort(key=lambda x: (pri.get(x["source"], 9), x["published"]))
        t["main"] = t["all"][0]
        t["score"] = max(x["score"] for x in t["all"])
        t["iwate"] = any("iwate" in x["areas"] for x in t["all"])
        t["published"] = min(x["published"] for x in t["all"])
        t["categories"] = list(dict.fromkeys(c for x in t["all"] for c in x["categories"]))
        t["keywords"] = list(dict.fromkeys(k for x in t["all"] for k in x["keywords"]))
        t["sources"] = list(dict.fromkeys(x["source"] for x in t["all"]))
    return topics


def primary_category(topic: dict, cat_conf: list[dict]) -> str:
    """重複掲載を避けるため、各話題を最も重みの大きい分野1つに振り分ける"""
    if not topic["categories"]:
        return "other"
    order = {c["id"]: (-c.get("weight", 1), i) for i, c in enumerate(cat_conf)}
    return min(topic["categories"], key=lambda c: order.get(c, (0, 999)))


def level(topic: dict) -> str:
    if topic["iwate"] and topic["score"] >= HIGH_SCORE:
        return "重要"
    if topic["score"] >= MID_SCORE:
        return "注目"
    return ""


# ============================================================ AI 概況（任意）
def ai_overview(topics: list[dict], labels: dict, period_label: str) -> list[str] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        print("[INFO] anthropic パッケージが無いため AI 概況は省略", file=sys.stderr)
        return None

    lines = []
    for t in sorted(topics, key=lambda x: -x["score"])[:120]:
        m = t["main"]
        cats = "・".join(labels.get(c, c) for c in t["categories"][:3])
        area = "岩手" if t["iwate"] else "県外"
        lines.append(f"- [{area}][{cats}] {m['title']}（{m['source']} {m['published'][:10]}）")
    prompt = (
        f"以下は{period_label}に報道された水産業・漁業関連ニュースの見出し一覧です。"
        "岩手県漁業協同組合連合会（県漁連）の職員が朝に読む報告書の冒頭に載せる「概況」を書いてください。\n\n"
        "要件:\n"
        "- 箇条書きで5〜8項目。各項目は「・」で始め、1〜2文（80字程度まで）。\n"
        "- 1項目目は岩手県・三陸の動きの総括。以降、魚種・養殖・市況・行政・環境など分野ごとに主要な動きをまとめる。\n"
        "- 県漁連・漁協の業務（資源、魚価、販売、経営、組合員の安全）に影響しそうな点があれば最後の項目で指摘する。\n"
        "- 見出しから読み取れる事実だけを使い、数値や固有名詞を推測で補わない。見出しに無い事柄は書かない。\n"
        "- 前置き・見出し・結びの文は書かず、箇条書きだけを出力する。\n\n"
        "見出し一覧:\n" + "\n".join(lines)
    )
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            print("[INFO] AI 概況は生成されませんでした（refusal）", file=sys.stderr)
            return None
        text = "".join(b.text for b in resp.content if b.type == "text")
    except anthropic.APIStatusError as e:
        print(f"[WARN] AI 概況の生成に失敗: {e.status_code} {e.message}", file=sys.stderr)
        return None
    except anthropic.APIConnectionError as e:
        print(f"[WARN] AI 概況の生成に失敗（接続）: {e}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001  AI 概況は任意機能なので、失敗しても報告書作成は続ける
        print(f"[WARN] AI 概況の生成に失敗: {e!r}", file=sys.stderr)
        return None
    items = [re.sub(r"^[・\-*•\s]+", "", ln).strip() for ln in text.splitlines()]
    return [i for i in items if i] or None


# ============================================================ Word 補助
def set_cell_bg(cell, hex_color: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def set_repeat_header(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def no_split_row(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:cantSplit")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def add_hyperlink(paragraph, text: str, url: str, bold=False, size=None, color=ACCENT):
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for k in ("w:ascii", "w:hAnsi", "w:eastAsia"):
        fonts.set(qn(k), FONT)
    rPr.append(fonts)
    if bold:
        rPr.append(OxmlElement("w:b"))
    c = OxmlElement("w:color")
    c.set(qn("w:val"), str(color))
    rPr.append(c)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "none")
    rPr.append(u)
    if size:
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size * 2)))
        rPr.append(sz)
    run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def run(p, text, bold=False, size=None, color=None):
    r = p.add_run(text)
    r.bold = bold
    if size:
        r.font.size = Pt(size)
    if color is not None:
        r.font.color.rgb = color
    return r


def para(doc, text="", style=None, size=None, color=None, bold=False, after=4, before=0, align=None):
    p = doc.add_paragraph(style=style)
    if text:
        run(p, text, bold=bold, size=size, color=color)
    pf = p.paragraph_format
    pf.space_after = Pt(after)
    pf.space_before = Pt(before)
    if align:
        p.alignment = align
    return p


def bottom_border(p, color="0B5D8C", sz=12):
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    b.set(qn("w:val"), "single")
    b.set(qn("w:sz"), str(sz))
    b.set(qn("w:space"), "4")
    b.set(qn("w:color"), color)
    bdr.append(b)
    # スキーマ上 pBdr は spacing/ind/jc より前に置く必要がある
    pPr.insert_element_before(bdr, "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
                              "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi",
                              "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
                              "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                              "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr",
                              "w:pPrChange")


def add_page_number_footer(section, label: str):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run(p, f"{label}　－ ", size=8, color=MUTED)
    r = p.add_run()
    r.font.size = Pt(8)
    for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), tag)
            r._r.append(fc)
        else:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = txt
            r._r.append(it)
    run(p, " －", size=8, color=MUTED)


def setup_styles(doc):
    st = doc.styles
    normal = st["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    for name, size, color in (("Title", 20, ACCENT), ("Heading 1", 14, ACCENT), ("Heading 2", 12, RGBColor(0x1C, 0x26, 0x30))):
        s = st[name]
        s.font.name = FONT
        s.font.size = Pt(size)
        s.font.bold = True
        s.font.color.rgb = color
        rpr = s.element.get_or_add_rPr()
        rpr.get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
        rpr.get_or_add_rFonts().set(qn("w:ascii"), FONT)
        rpr.get_or_add_rFonts().set(qn("w:hAnsi"), FONT)
        pf = s.paragraph_format
        pf.space_before = Pt(14 if name != "Title" else 0)
        pf.space_after = Pt(6)
        pf.keep_with_next = True
    for name in ("List Bullet", "List Number"):
        s = st[name]
        s.font.name = FONT
        s.font.size = Pt(10)


def jdate(d: datetime) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def fmt_dt(iso: str, with_time=True) -> str:
    d = datetime.fromisoformat(iso).astimezone(JST)
    w = "月火水木金土日"[d.weekday()]
    return f"{d.month}/{d.day}({w})" + (f" {d:%H:%M}" if with_time else "")


def make_table(doc, widths_mm: list[float], header: list[str]):
    tbl = doc.add_table(rows=1, cols=len(header))
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    for i, w in enumerate(widths_mm):
        tbl.columns[i].width = Mm(w)
    hdr = tbl.rows[0]
    set_repeat_header(hdr)
    for i, (cell, h) in enumerate(zip(hdr.cells, header)):
        cell.width = Mm(widths_mm[i])
        set_cell_bg(cell, "E3F0F8")
        p = cell.paragraphs[0]
        run(p, h, bold=True, size=9)
    return tbl


def add_row(tbl, widths_mm):
    row = tbl.add_row()
    no_split_row(row)
    for i, c in enumerate(row.cells):
        c.width = Mm(widths_mm[i])
    return row


# ============================================================ 報告書本体
def build(days: int, now: datetime | None = None) -> Path:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    kw = load_keywords()
    labels = dict(data.get("category_labels") or {})
    labels["other"] = "その他（水産全般）"
    cat_conf = kw.get("categories", [])

    if now is None:
        now = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(JST)
    since = now - timedelta(days=days)
    # 期間内に公開された記事 ＋ 期間内に新たに見つかった記事（配信が遅れた記事の取りこぼし防止）
    arts = [a for a in data["articles"]
            if datetime.fromisoformat(a["published"]) >= since
            or datetime.fromisoformat(a.get("first_seen", a["published"])) >= since]
    # 最新のキーワード設定で再分類（keywords.yaml の変更を過去記事にも反映）
    sys.path.insert(0, str(ROOT / "scripts"))
    from collect import Classifier
    clf = Classifier(kw)
    for a in arts:
        info = clf.classify(a["title"], a.get("summary", ""))
        a.update({k: info[k] for k in ("score", "areas", "categories", "keywords")})
    topics = cluster(arts)
    kind, slug = KINDS.get(days, (f"{days}日間", f"{days}days"))
    period = f"{fmt_dt(since.isoformat())} 〜 {fmt_dt(now.isoformat())}"

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.left_margin = sec.right_margin = Mm(18)
    sec.top_margin = sec.bottom_margin = Mm(16)
    setup_styles(doc)
    add_page_number_footer(sec, f"水産・漁業ニュース{kind}（{jdate(now)}）")
    content_w = 210 - 36

    # ---------------- 表紙ブロック
    p = para(doc, f"水産・漁業ニュース{kind}", style="Title", after=2)
    p = para(doc, "岩手・三陸版", size=11, color=ACCENT, bold=True, after=2)
    bottom_border(p)
    iwate_n = sum(1 for t in topics if t["iwate"])
    p = para(doc, after=2, before=6)
    run(p, "対象期間　", bold=True, size=9)
    run(p, period, size=9)
    p = para(doc, after=2)
    run(p, "作成日時　", bold=True, size=9)
    run(p, f"{jdate(datetime.now(JST))} {datetime.now(JST):%H:%M}（自動作成）", size=9)
    p = para(doc, after=10)
    run(p, "収録件数　", bold=True, size=9)
    run(p, f"{len(topics)} 話題（記事 {len(arts)} 件）／ うち岩手・三陸関連 {iwate_n} 話題", size=9)

    if not topics:
        para(doc, "対象期間に該当する記事はありませんでした。", size=11)
        return save(doc, slug, kind, now, days, 0, 0)

    # ---------------- 1. 概況
    doc.add_heading("1. 概況", level=1)
    ai = ai_overview(topics, labels, f"{kind}の対象期間（{period}）")
    if ai:
        p = para(doc, "AIによる概況まとめ（見出しから自動生成。詳細は各記事でご確認ください）", size=8, color=MUTED, after=2)
        for line in ai:
            bp = doc.add_paragraph(style="List Bullet")
            run(bp, line, size=10)
            bp.paragraph_format.space_after = Pt(2)
        para(doc, after=4)

    by_cat = defaultdict(list)
    for t in topics:
        by_cat[primary_category(t, cat_conf)].append(t)
    cat_order = [c["id"] for c in cat_conf] + ["other"]

    # 要点（ルールベース）
    kw_counter = Counter(k for t in topics for k in t["keywords"]
                         if k not in ("岩手", "三陸", "漁業", "水産", "漁協", "漁連"))
    top_kw = "、".join(k for k, _ in kw_counter.most_common(8))
    busiest = sorted(((c, len(v)) for c, v in by_cat.items() if c != "other"), key=lambda x: -x[1])[:3]
    src_counter = Counter(s for t in topics for s in t["sources"])
    points = [
        f"収録 {len(topics)} 話題のうち、岩手・三陸に関する話題は {iwate_n} 件"
        f"（「重要」{sum(1 for t in topics if level(t) == '重要')} 件、「注目」{sum(1 for t in topics if level(t) == '注目')} 件）。",
        "話題の多い分野：" + "、".join(f"{labels.get(c, c)}（{n}件）" for c, n in busiest) + "。",
        f"よく出たキーワード：{top_kw}。" if top_kw else "",
        "主な掲載媒体：" + "、".join(f"{s}（{n}）" for s, n in src_counter.most_common(6)) + "。",
    ]
    if not ai:
        para(doc, "要点", bold=True, size=10, after=2)
    for pt in filter(None, points):
        bp = doc.add_paragraph(style="List Bullet")
        run(bp, pt, size=9.5 if ai else 10, color=MUTED if ai else None)
        bp.paragraph_format.space_after = Pt(2)

    # 分野別件数表
    para(doc, after=2)
    widths = [48, 18, 24, 84]
    tbl = make_table(doc, widths, ["分野", "話題数", "うち岩手・三陸", "最重要の話題"])
    for c in cat_order:
        ts = by_cat.get(c)
        if not ts:
            continue
        row = add_row(tbl, widths)
        top = max(ts, key=lambda x: x["score"])
        vals = [labels.get(c, c), str(len(ts)), str(sum(1 for t in ts if t["iwate"])),
                (top["main"]["title"][:34] + "…") if len(top["main"]["title"]) > 34 else top["main"]["title"]]
        for i, v in enumerate(vals):
            pr = row.cells[i].paragraphs[0]
            run(pr, v, size=8.5)
            if i in (1, 2):
                pr.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # ---------------- 2. 岩手・三陸の主要ニュース
    doc.add_heading("2. 岩手・三陸の主要ニュース", level=1)
    para(doc, "岩手県・三陸沿岸に関する話題を重要度順に掲載（同じ出来事の他媒体記事はまとめて表示）。",
         size=8.5, color=MUTED, after=6)
    iw = sorted([t for t in topics if t["iwate"]], key=lambda x: (-x["score"], x["published"]))
    if not iw:
        para(doc, "該当する話題はありませんでした。", size=10)
    for n, t in enumerate(iw[:25], 1):
        write_topic(doc, n, t, labels)
    if len(iw) > 25:
        para(doc, f"ほか {len(iw) - 25} 件は「3. 分野別ニュース一覧」に掲載しています。", size=8.5, color=MUTED)

    # ---------------- 3. 分野別ニュース一覧（全話題・重複なし）
    doc.add_heading("3. 分野別ニュース一覧（全件）", level=1)
    para(doc, "収録した全話題を分野ごとに掲載（各話題は最も関係の深い1分野にのみ掲載）。"
         "★＝岩手・三陸関連、見出しをクリックすると記事を開きます。", size=8.5, color=MUTED, after=4)
    widths = [22, 112, 40]
    for c in cat_order:
        ts = by_cat.get(c)
        if not ts:
            continue
        ts.sort(key=lambda x: (not x["iwate"], -x["score"], x["published"]))
        doc.add_heading(f"{labels.get(c, c)}（{len(ts)}件）", level=2)
        tbl = make_table(doc, widths, ["日付", "見出し", "媒体"])
        for t in ts:
            row = add_row(tbl, widths)
            m = t["main"]
            run(row.cells[0].paragraphs[0], fmt_dt(m["published"], with_time=False), size=8.5)
            pr = row.cells[1].paragraphs[0]
            lv = level(t)
            if t["iwate"]:
                run(pr, "★", size=9, color=HIGH, bold=True)
            if lv:
                run(pr, f"[{lv}] ", size=8, color=HIGH if lv == "重要" else RGBColor(0xA1, 0x62, 0x07), bold=True)
            add_hyperlink(pr, m["title"], m["url"], size=9, color=RGBColor(0x1C, 0x26, 0x30))
            pr2 = row.cells[2].paragraphs[0]
            run(pr2, m["source"], size=8.5)
            if t["others"]:
                run(pr2, f" ほか{len(t['sources']) - 1}媒体" if len(t["sources"]) > 1 else f" ほか{len(t['others'])}件",
                    size=7.5, color=MUTED)

    # ---------------- 4. 収集状況
    doc.add_heading("4. 収集状況・注記", level=1)
    widths = [100, 37, 37]
    tbl = make_table(doc, widths, ["取得元", "状態", "採用 / 取得"])
    for s in data.get("sources", []):
        row = add_row(tbl, widths)
        run(row.cells[0].paragraphs[0], s["name"], size=8)
        run(row.cells[1].paragraphs[0], "正常" if s.get("ok") else "取得失敗", size=8,
            color=None if s.get("ok") else HIGH)
        run(row.cells[2].paragraphs[0],
            f"{s.get('kept', 0)} / {s.get('fetched', 0)}" if s.get("ok") else (s.get("error", "")[:30]), size=8)
    para(doc, after=4)
    for note in (
        "記事の見出し・リンクは Google ニュース検索および各社の公開RSSから自動収集しています。本文は各媒体サイトでご確認ください"
        "（岩手日報・水産経済新聞・みなと新聞などは会員限定記事の場合があります）。",
        "「重要」「注目」は、地域名（岩手・三陸の沿岸市町村など）・漁連/漁協・魚種・テーマのキーワードによる自動判定です。",
        "同じ出来事を報じた複数媒体の記事は1話題にまとめ、代表記事（岩手日報・朝日新聞などを優先）を表示しています。",
        f"収集データ最終更新：{fmt_dt(data['updated_at'])}。",
    ):
        bp = doc.add_paragraph(style="List Bullet")
        run(bp, note, size=8, color=MUTED)
        bp.paragraph_format.space_after = Pt(1)

    return save(doc, slug, kind, now, days, len(topics), len(arts))


def write_topic(doc, n: int, t: dict, labels: dict):
    m = t["main"]
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.keep_with_next = True
    run(p, f"{n}. ", bold=True, size=10.5)
    lv = level(t)
    if lv:
        run(p, f"【{lv}】", bold=True, size=10, color=HIGH if lv == "重要" else RGBColor(0xA1, 0x62, 0x07))
    add_hyperlink(p, m["title"], m["url"], bold=True, size=10.5, color=RGBColor(0x1C, 0x26, 0x30))

    meta = doc.add_paragraph()
    meta.paragraph_format.left_indent = Mm(5)
    meta.paragraph_format.space_after = Pt(1)
    meta.paragraph_format.keep_with_next = bool(m.get("summary") or t["others"])
    cats = "・".join(labels.get(c, c) for c in t["categories"][:4])
    run(meta, f"{m['source']}　{fmt_dt(m['published'])}", size=8.5, color=MUTED)
    if cats:
        run(meta, f"　｜　{cats}", size=8.5, color=ACCENT)
    if t["keywords"]:
        run(meta, f"　｜　キーワード：{'・'.join(t['keywords'][:6])}", size=8.5, color=MUTED)

    if m.get("summary"):
        sp = doc.add_paragraph()
        sp.paragraph_format.left_indent = Mm(5)
        sp.paragraph_format.space_after = Pt(1)
        run(sp, m["summary"], size=9)

    if t["others"]:
        op = doc.add_paragraph()
        op.paragraph_format.left_indent = Mm(5)
        op.paragraph_format.space_after = Pt(1)
        run(op, "関連報道：", size=8.5, color=MUTED)
        for i, o in enumerate(t["all"][1:6]):
            if i:
                run(op, "、", size=8.5, color=MUTED)
            add_hyperlink(op, o["source"], o["url"], size=8.5)
        if len(t["all"]) > 6:
            run(op, f" ほか{len(t['all']) - 6}件", size=8.5, color=MUTED)


def save(doc, slug, kind, now, days, n_topics, n_arts) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"水産ニュース_{kind}_{now:%Y-%m-%d}.docx"
    path = OUT_DIR / name
    doc.core_properties.title = f"水産・漁業ニュース{kind}（岩手・三陸版）{now:%Y-%m-%d}"
    doc.core_properties.author = "水産ニュース自動収集"
    # python-docx 既定テンプレートの zoom 要素に必須属性が無いため補う（Word の厳密チェック対策）
    for z in doc.settings.element.iter(qn("w:zoom")):
        if z.get(qn("w:percent")) is None:
            z.set(qn("w:percent"), "100")
    doc.save(path)
    shutil.copyfile(path, OUT_DIR / f"latest_{slug}.docx")

    # 一覧（ページ表示用）を更新。古いファイルは保存日数を超えたら削除
    idx_path = OUT_DIR / "index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        idx = {"reports": []}
    idx["reports"] = [r for r in idx["reports"] if r["file"] != name]
    idx["reports"].append({"file": name, "kind": kind, "slug": slug, "date": f"{now:%Y-%m-%d}",
                           "days": days, "topics": n_topics, "articles": n_arts,
                           "created": datetime.now(JST).isoformat(timespec="minutes")})
    keep_until = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    for r in [r for r in idx["reports"] if r["date"] < keep_until]:
        (OUT_DIR / r["file"]).unlink(missing_ok=True)
    idx["reports"] = sorted([r for r in idx["reports"] if r["date"] >= keep_until],
                            key=lambda r: (r["date"], r["days"]), reverse=True)
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"作成: {path.name}（{n_topics} 話題 / {n_arts} 記事）")
    return path


def main() -> int:
    global OUT_DIR, DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, nargs="+", default=[1], help="対象日数（例: 1 / 7 / 30、複数指定可）")
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    DATA, OUT_DIR = args.data, args.out
    for d in args.days:
        build(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
