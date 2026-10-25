# -*- coding: utf-8 -*-
"""
check_extra.py
check_script.py に足す検査（2026-10-24 のミスを止める）。
check_script.py と同じフォルダ（jinja\）に置き、check_script.py の最後で
    from check_extra import run_extra
    ng += run_extra(script_id)
のように呼ぶ。NG は「行番号: 中身」の文字列のリストで返す。

台本JSON・出典.json の項目名が違うときは、下の「項目名」の所だけ合わせる。
"""
import json
import os
import re
import glob
from datetime import datetime

# ===== 場所（jinja\ からの相対） =====
BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, "scripts")
PHOTOS = os.path.join(BASE, "photos")

# ===== 項目名（合わない時はここだけ直す） =====
K_LINES = "lines"        # 台本JSONの行の配列
K_NAR = "nar"            # ナレーション
K_TEL = "tel"            # 字幕（強調の指定を含む）
K_GPT = "gpt"            # GPTを通した記録 {"相談","時刻","範囲"}
SRC_FILE = "出典.json"    # photos\<順>_<id>\出典.json

# 使ってよい素材の取り元
OK_SOURCES = ["写真AC", "動画AC", "ぱくたそ", "Wikimedia", "Pexels", "自前", "AI"]
# Wikimedia で使ってよいライセンス
OK_WIKI_LICENSE = ["CC0", "PD", "Public domain", "パブリックドメイン"]
# 素材の拡張子
MEDIA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov")
# AIの上限（1本に3枚くらいまで）
AI_MAX = 3


def _load(path):
    """JSONを読む。壊れていたら None"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _photo_dir(script_id):
    """photos/<投稿順>_<id> を探す"""
    hits = glob.glob(os.path.join(PHOTOS, "*_" + script_id))
    return hits[0] if hits else None


def _order_num(folder):
    """photos のフォルダ名の頭の数字（投稿順）"""
    m = re.match(r"(\d+)_", os.path.basename(folder))
    return int(m.group(1)) if m else None


def _src_rows(src):
    """出典.json を {行番号(int): 中身} にそろえる（dict でも list でも読む）"""
    rows = {}
    if isinstance(src, dict):
        for k, v in src.items():
            try:
                rows[int(re.sub(r"\D", "", str(k)))] = v
            except ValueError:
                pass
    elif isinstance(src, list):
        for i, v in enumerate(src, 1):
            n = v.get("line", i) if isinstance(v, dict) else i
            rows[int(n)] = v
    return rows


def _sen(script):
    """題から 3選/5選 を取り出す"""
    title = ""
    lines = script.get(K_LINES, [])
    if lines:
        title = lines[0].get(K_NAR, "") if isinstance(lines[0], dict) else str(lines[0])
    title = script.get("title", title)
    m = re.search(r"(\d)選", title.replace(" ", ""))
    return m.group(1) if m else None


def run_extra(script_id):
    ng = []
    spath = os.path.join(SCRIPTS, script_id + ".json")
    script = _load(spath)
    if script is None:
        return ["台本JSONが読めません: " + spath]
    lines = script.get(K_LINES, [])
    pdir = _photo_dir(script_id)
    if pdir is None:
        return ["photos に *_%s のフォルダがありません" % script_id]
    src_path = os.path.join(pdir, SRC_FILE)
    src = _src_rows(_load(src_path) or {})

    # ---- 1. GPTを通したか ----
    g = script.get(K_GPT)
    if not g or not g.get("時刻"):
        ng.append("全体: GPTに通した記録（gpt）がありません")
    else:
        try:
            t = datetime.fromisoformat(g["時刻"]).timestamp()
            for p in (spath, src_path):
                if os.path.exists(p) and os.path.getmtime(p) > t + 60:
                    ng.append("全体: %s がGPTに通したあと（%s）に直されています。もう一度GPTに通してください"
                              % (os.path.basename(p), g.get("相談", "")))
        except ValueError:
            ng.append("全体: gpt.時刻 の形が違います（例 2026-10-24T21:00）")

    # ---- 2. 素材がそろっているか ----
    files = os.listdir(pdir)
    for i in range(1, len(lines) + 1):
        nn = "%02d" % i
        has = any(f.startswith(nn) and f.lower().endswith(MEDIA_EXT) for f in files)
        if not has:
            ng.append("%s行: 素材がありません" % nn)
        if i not in src:
            ng.append("%s行: 出典.json にこの行がありません" % nn)

    # ---- 3. 別の台本の素材・「いつもの」を指していないか ----
    for n, row in src.items():
        if not isinstance(row, dict):
            continue
        for key in ("file", "path", "from"):
            v = str(row.get(key, ""))
            if not v:
                continue
            if "いつもの" in v:
                ng.append("%02d行: 素材の場所が「いつもの」です。ファイル名で指定してください" % n)
            m = re.search(r"photos[\\/](\d+_[A-Za-z0-9_]+)", v)
            if m and m.group(1) != os.path.basename(pdir):
                ng.append("%02d行: 別の台本（%s）の素材を指しています" % (n, m.group(1)))

    # ---- 4. 出典のURLが検索ページでないか ----
    for n, row in src.items():
        if not isinstance(row, dict):
            continue
        url = str(row.get("url", ""))
        if url and re.search(r"search|[?&]q=|/s/|keyword=", url, re.I):
            ng.append("%02d行: 出典のURLが検索ページです。写真の個別ページにしてください: %s" % (n, url))

    # ---- 5. 取り元とライセンス ----
    ai = 0
    for n, row in src.items():
        if not isinstance(row, dict):
            continue
        s = str(row.get("source", ""))
        if not any(o in s for o in OK_SOURCES):
            ng.append("%02d行: 使えない取り元です（%s）" % (n, s or "空"))
        if "Wikimedia" in s:
            lic = str(row.get("license", ""))
            if not any(o.lower() in lic.lower() for o in OK_WIKI_LICENSE):
                ng.append("%02d行: Wikimedia は CC0/PD だけです（license=%s）" % (n, lic or "空"))
        # ---- 6. AIの数と「イメージ」表記 ----
        if s.startswith("AI") or "AI" == s:
            ai += 1
            if "イメージ" not in str(row.get("text", "")):
                ng.append("%02d行: AI画像なのに「イメージ」表記がありません" % n)
    if ai > AI_MAX:
        ng.append("全体: AI画像が%d枚あります（%d枚まで）" % (ai, AI_MAX))

    # ---- 7. 3選と5選が交互か ----
    me = _order_num(pdir)
    if me:
        prev = glob.glob(os.path.join(PHOTOS, "%d_*" % (me - 1)))
        if prev:
            pid = os.path.basename(prev[0]).split("_", 1)[1]
            ps = _load(os.path.join(SCRIPTS, pid + ".json"))
            if ps and _sen(ps) and _sen(ps) == _sen(script):
                ng.append("全体: 前の台本（%s）も%s選です。3選と5選は交互" % (pid, _sen(script)))

    # ---- 8. 字幕の帯（表紙＝1行目以外に帯があればNG） ----
    for i, ln in enumerate(lines, 1):
        if i == 1 or not isinstance(ln, dict):
            continue
        tel = json.dumps(ln.get(K_TEL, ""), ensure_ascii=False)
        if re.search(r'"(band|box|bg|帯)"\s*:\s*(true|"[^"]+")', tel):
            ng.append("%02d行: 表紙以外の字幕に帯があります" % i)

    # ---- 9. 赤い背景に赤い強調（出典.json の bgColor に「赤」「朱」と書いた行） ----
    for i, ln in enumerate(lines, 1):
        if not isinstance(ln, dict):
            continue
        tel = json.dumps(ln.get(K_TEL, ""), ensure_ascii=False)
        red = "#FF2A2A" in tel.upper() or "(赤)" in tel or "赤" in tel and "強調" in tel
        bg = str(src.get(i, {}).get("bgColor", "")) if isinstance(src.get(i), dict) else ""
        if red and any(c in bg for c in ("赤", "朱")):
            ng.append("%02d行: 赤・朱の背景に赤い強調文字があります" % i)

    # ---- 10. コメントの位置（1社目の締めのすぐあと） ----
    narr = [ln.get(K_NAR, "") if isinstance(ln, dict) else str(ln) for ln in lines]
    c_idx = next((i for i, t in enumerate(narr) if "コメントに" in t), None)
    s2_idx = next((i for i, t in enumerate(narr) if t.startswith("2社目")), None)
    if c_idx is None:
        ng.append("全体: コメントの呼びかけの行がありません")
    elif s2_idx is not None and c_idx != s2_idx - 1:
        ng.append("%02d行: コメントは1社目の締めのすぐあと（2社目の直前）に置きます" % (c_idx + 1))

    return ng


if __name__ == "__main__":
    import sys
    for sid in sys.argv[1:]:
        r = run_extra(sid)
        print("== %s  追加検査 NG %d件" % (sid, len(r)))
        for x in r:
            print("  " + x)
