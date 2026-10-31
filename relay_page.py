#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relay_page.py ─ DMM(FANZA)アフィリエイト「中継ページ」自動生成モジュール
=====================================================================
【役割】
 X投稿のリンク先を、FANZAのアフィリンク直張りではなく
 「自分で持つ中継ページ(記事ページ)」にするための部品。
 GitHub Pages(無料・公式)に置くことを前提に、
  1) DMM APIから作品を取得
  2) 記事HTMLを自動生成 (docs/articles/xxxx.html)
  3) index.json / index.html を更新
  4) 投稿済み作品IDを published_ids.txt に記録
 までを一括で行う。GitHub Actionsのcronから呼ばれる想定。

【使い方】
  バッチ生成（GitHub Actionsから）:
      python3 relay_page.py --batch 3

  MacのXボットからは「index.json を読むだけ」で連携する
  （main_improved.py の RELAY_MODE = "github_pages"）。

【必要な設定（環境変数）】
  DMM_API_ID        DMMアフィリエイト API ID
  DMM_AFFILIATE_ID  アフィリエイトID
  RELAY_SITE_URL    公開URLのベース 例: https://yourname.github.io/fanza-relay
  RELAY_OUTPUT_DIR  出力先（既定: docs → GitHub Pages の「main/docs」でそのまま使える）

【注意】
 ・noteには公式の投稿APIが存在しないため、noteへの自動投稿はこのモジュールでは
   行いません（規約リスクの詳細はセットアップガイド参照）。
 ・Blogger(公式APIあり・無料)を使う場合は下の blogger_post() を利用できます。
"""

import os
import re
import sys
import json
import html
import random
import urllib.request
from datetime import datetime, timedelta, timezone

# =====================================================================
# 【設定エリア】
# =====================================================================
API_ID = os.environ.get("DMM_API_ID", "")
AFFILIATE_ID = os.environ.get("DMM_AFFILIATE_ID", "")

# 公開ベースURL（末尾スラッシュ不要）。例: https://yourname.github.io/fanza-relay
SITE_BASE_URL = os.environ.get("RELAY_SITE_URL", "").rstrip("/")

# 出力先フォルダ。GitHub Pages を「main ブランチ / docs フォルダ」で公開する前提
OUTPUT_DIR = os.environ.get("RELAY_OUTPUT_DIR", "docs")

# 投稿済み(記事化済み)作品IDの記録ファイル（リポジトップに置き、Actionsでコミットされる）
PUBLISHED_FILE = os.environ.get("RELAY_PUBLISHED_FILE", "published_ids.txt")

# 1回のバッチで作る記事数（1日3記事 → 月約90記事）
BATCH_SIZE = 3

# ランキングの取得範囲
RANK_MIN = 50
RANK_MAX = 300

JST = timezone(timedelta(hours=9))
# =====================================================================


# ---------------------------------------------------------------------
# DMM API
# ---------------------------------------------------------------------
def fetch_fanza_item(offset: int):
    """作品を1件取得してdictで返す（失敗時 None）"""
    api_url = (
        f"https://api.dmm.com/affiliate/v3/ItemList?"
        f"api_id={API_ID}&affiliate_id={AFFILIATE_ID}&"
        f"site=FANZA&service=digital&floor=videoa&"
        f"sort=rank&hits=1&offset={offset}&output=json"
    )
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            items = json.loads(resp.read().decode("utf-8")).get("result", {}).get("items", [])
        return items[0] if items else None
    except Exception as e:
        print(f"[relay_page] API取得エラー(offset={offset}): {e}")
        return None


def _load_published():
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return {l.strip() for l in f if l.strip()}
    except FileNotFoundError:
        return set()


def _save_published(cid: str):
    with open(PUBLISHED_FILE, "a", encoding="utf-8") as f:
        f.write(cid + "\n")


# ---------------------------------------------------------------------
# 記事HTML生成
# ---------------------------------------------------------------------
_PAGE_TMPL = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noarchive">
<title>{title}</title>
</head>
<body style="font-family:sans-serif;max-width:720px;margin:0 auto;padding:16px;">
<header><h1 style="font-size:1.3em;">{title}</h1></header>
<main>
<p style="color:#b00;font-weight:bold;">⚠️ このページには成人向けコンテンツへのリンクが含まれます。18歳未満の方は閲覧できません。</p>
<img src="{img}" alt="{title}" style="width:100%;max-width:480px;display:block;margin:12px auto;">
<dl>
 <dt><b>出演</b></dt><dd>{actress}</dd>
 <dt><b>ジャンル</b></dt><dd>{genres}</dd>
</dl>
<p>{comment}</p>
<p style="text-align:center;margin:24px 0;">
 <a href="{aff_url}" rel="nofollow sponsored"
    style="display:inline-block;padding:14px 28px;background:#d63384;color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;">
   {link_label}
 </a>
</p>
<p style="font-size:0.8em;color:#666;">※このページはDMMアフィリエイトを利用しており、紹介リンク経由の登録・購入により当サイトに報酬が発生します。</p>
<p><a href="{index_url}">← 記事一覧へ戻る</a></p>
</main>
<footer style="font-size:0.75em;color:#999;"><small>generated: {now}</small></footer>
</body>
</html>
"""


def _names(item, key):
    info = item.get("iteminfo") or {}
    return [e.get("name", "") for e in (info.get(key) or []) if isinstance(e, dict) and e.get("name")]


def _to_str(v) -> str:
    """★DMM APIの型ゆらぎ対策: dictや予期しない型が来ても必ず文字列に正規化する。
    （AttributeError: 'dict' object has no attribute 'replace' の修正）"""
    if isinstance(v, dict):
        v = v.get("url") or v.get("large") or v.get("small") or ""
    if not isinstance(v, str):
        v = str(v) if v is not None else ""
    return v


def create_article(item) -> dict:
    """作品1件ぶんの記事HTMLを書き出し、index登録用のdictを返す"""
    cid = _to_str(item.get("content_id")) or datetime.now(JST).strftime("%Y%m%d%H%M%S")
    title = _to_str(item.get("title")) or "作品紹介"
    aff_url = _to_str(item.get("affiliateURL"))
    img = _to_str(item.get("imageURL_large") or item.get("imageURL"))
    actress = "、".join(_names(item, "actress")[:3]) or "─"
    genres = "、".join(_names(item, "genre")[:5]) or "─"
    comment = re.sub(r"\s+", " ", _to_str(item.get("comment"))).strip()

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    article_dir = os.path.join(OUTPUT_DIR, "articles")
    os.makedirs(article_dir, exist_ok=True)

    fname = f"{cid}.html"
    article_rel = f"articles/{fname}"
    article_url = f"{SITE_BASE_URL}/{article_rel}" if SITE_BASE_URL else article_rel
    index_url = f"{SITE_BASE_URL}/index.html" if SITE_BASE_URL else "index.html"

    page = _PAGE_TMPL.format(
        title=html.escape(title[:80]),
        img=html.escape(img),
        actress=html.escape(actress),
        genres=html.escape(genres),
        comment=html.escape(comment[:600]),
        aff_url=html.escape(aff_url, quote=True),
        link_label="作品ページを見る（FANZA）",
        index_url=html.escape(index_url, quote=True),
        now=now,
    )
    with open(os.path.join(article_dir, fname), "w", encoding="utf-8") as f:
        f.write(page)

    return {
        "content_id": cid,
        "title": title[:60],
        "actress": actress,
        "url": article_url,
        "affiliate_url": aff_url,
        "date": datetime.now(JST).strftime("%Y-%m-%d"),
    }


def update_index(entries):
    """index.json（機械 readable・新しい順）と index.html（人間用）を書き出す"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # --- index.json ---
    existing = []
    idx_path = os.path.join(OUTPUT_DIR, "index.json")
    if os.path.exists(idx_path):
        try:
            with open(idx_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []
    by_cid = {e["content_id"]: e for e in existing}
    for e in entries:
        by_cid[e["content_id"]] = e
    merged = sorted(by_cid.values(), key=lambda e: e.get("date", ""), reverse=True)
    # 直近120件だけ残す（ファイル肥大防止）
    merged = merged[:120]
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)

    # --- index.html ---
    rows = "\n".join(
        f'<li><a href="{html.escape(e["url"], quote=True)}">{html.escape(e["title"])}</a>'
        f' <small>({html.escape(e.get("actress",""))} / {html.escape(e.get("date",""))})</small></li>'
        for e in merged
    )
    index_html = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FANZA新作レビュー記事一覧</title></head>
<body style="font-family:sans-serif;max-width:720px;margin:0 auto;padding:16px;">
<h1>FANZA新作レビュー記事一覧</h1>
<p style="color:#b00;font-weight:bold;">⚠️ 成人向けコンテンツを扱います。18歳未満の方は閲覧できません。</p>
<ul>{rows}</ul>
<p style="font-size:0.8em;color:#666;">※当サイトはDMMアフィリエイトを利用しています。</p>
</body></html>"""
    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)


def run_batch(n: int = BATCH_SIZE):
    """n件ぶんの新規記事を生成する（GitHub Actions から呼ぶメイン関数）"""
    published = _load_published()
    created = []
    tried = 0
    while len(created) < n and tried < n * 6:
        tried += 1
        item = fetch_fanza_item(random.randint(RANK_MIN, RANK_MAX))
        if not item:
            continue
        cid = str(item.get("content_id", ""))
        if not cid or cid in published:
            continue
        try:
            entry = create_article(item)
        except Exception as e:
            # ★1作品の生成失敗でバッチ全体が止まらないようにする
            print(f"[relay_page] 記事生成スキップ(content_id={cid}): {e}")
            continue
        _save_published(cid)
        published.add(cid)
        created.append(entry)
        print(f"[relay_page] 記事生成: {entry['title']}")
    update_index(created)
    print(f"[relay_page] 完了: {len(created)}件生成 / 試行{tried}回")
    return created


# ---------------------------------------------------------------------
# （オプションB）Blogger 公式API での投稿
#  Blogger は公式APIが提供されており無料で使える。
#  事前準備: Google Cloud で Blogger API を有効化し、OAuthクライアントIDを作成、
#  pip install --upgrade google-api-python-client google-auth-oauthlib
#  初回実行時にブラウザ認証 → token.json 保存（Mac側でのみ実行可）
# ---------------------------------------------------------------------
BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")   # BloggerのブログID

def blogger_post(title: str, html_body: str) -> str:
    """Bloggerに公式APIで記事を投稿し、公開URLを返す（未設定時は空文字）"""
    if not BLOGGER_BLOG_ID:
        return ""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("[relay_page] google-api-python-client が未インストールです")
        return ""
    scopes = ["https://www.googleapis.com/auth/blogger"]
    token_file = "blogger_token.json"
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, scopes)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", scopes)
        creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    service = build("blogger", "v3", credentials=creds)
    post = service.posts().insert(
        blogId=BLOGGER_BLOG_ID,
        body={"kind": "blogger#post", "title": title, "content": html_body},
        isDraft=False,
    ).execute()
    return post.get("url", "")


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
if __name__ == "__main__":
    n = BATCH_SIZE
    if "--batch" in sys.argv:
        i = sys.argv.index("--batch")
        if i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
    if not API_ID or not AFFILIATE_ID:
        print("[relay_page] DMM_API_ID / DMM_AFFILIATE_ID が未設定です")
        sys.exit(1)
    run_batch(n)
