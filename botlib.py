#!/usr/bin/env python3
"""threads-bot 共通基盤（botlib）

LINE通知・JSON状態ファイルなど、各スクリプトに重複していた処理を集約する。
標準ライブラリのみに依存（pip追加インストール不要＝どのワークフローからも使える）。

移行済み: like_auto.py / oauth_reminder.py / research_threads.py /
  post_saas.py（_notify_line）/ post_api.py（状態JSON3関数）
移行しない（意図的）:
- generate_posts.py / generate_saas_posts.py … 投稿プールのJSON読み込みは
  「壊れていたら落とす」設計が正（黙って空データで続行するとプールを壊すため）。
  load_json のデフォルト返却とは意味が異なるので置き換えない
- preview_gen.py … LINE/状態JSONを持たない
- saas_app/（Vercel関数）… デプロイ単位が別のため対象外
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

LINE_API = "https://api.line.me/v2/bot/message"


# ── JSON状態ファイル ──────────────────────────────────────────
def load_json(path: str, default):
    """JSONファイルを読む。無い・壊れている場合は default を返す。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── LINE通知 ──────────────────────────────────────────────────
def _default_token() -> str:
    """管理者通知の既定トークン: Claude通知Bot優先 → とうこさんLINEにフォールバック"""
    return os.environ.get("ADMIN_NOTIFY_LINE_TOKEN") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


def line_broadcast(text: str, token: str = "", *, timeout: int = 10) -> bool:
    """LINE broadcast（管理者向け通知）。失敗しても例外を投げず、ログを残して False。
    token 未指定時は ADMIN_NOTIFY_LINE_TOKEN → LINE_CHANNEL_ACCESS_TOKEN の順で解決。"""
    token = token or _default_token()
    if not token:
        print(f"[line] トークン無しのため未送信: {text[:60]}", file=sys.stderr)
        return False
    try:
        body = json.dumps({"messages": [{"type": "text", "text": text}]}).encode()
        req = urllib.request.Request(
            f"{LINE_API}/broadcast",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception as e:
        print(f"[line] broadcast失敗（処理は継続）: {e}", file=sys.stderr)
        return False


def line_push(user_id: str, text: str, token: str, *, timeout: int = 10) -> int:
    """特定ユーザーへのLINE push（クライアント宛て等）。失敗時は例外を投げる
    （宛先指定の送信は黙って失敗させない）。戻り値はHTTPステータス。"""
    body = json.dumps({"to": user_id, "messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request(
        f"{LINE_API}/push",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status
