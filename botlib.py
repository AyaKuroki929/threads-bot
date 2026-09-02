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
    """JSONファイルを読む。無い場合は default。壊れている場合も default だが、
    「ファイルなし」と区別できるよう stderr に警告を出す（静かな状態巻き戻りの検知用）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"[botlib] JSON破損を検出（defaultで継続）: {path}: {e}", file=sys.stderr)
        return default


def save_json(path: str, data) -> None:
    """一時ファイル→os.replace のアトミック書き込み。
    直書きだと書き込み途中の kill でファイルが壊れ、次回読み込みが黙って
    初期状態に巻き戻る（used_posts喪失→過去ネタの重複投稿の温床）。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── 生成コンテンツの最終バリデーション ─────────────────────────
# Claude生成の投稿がプールに入る直前の防波堤。プロンプト側で禁止していても
# 生成グリッチ・指示逸脱は起きるため、コード側で機械的に落とす。
NG_CONTENT_PATTERNS = (
    # 薬機法・景表法系の断定/保証（エステ・ダイエット商材で特にリスク）
    r"治[るりしせ]|治療|完治",
    r"(シミ|しみ|シワ|しわ|セルライト)が消え",
    r"絶対(に)?(痩せ|効果|結果|変わ)",
    r"必ず(痩せ|効果|結果)",
    r"100\s*[%％]痩せ|１００\s*[%％]",
    r"効果を保証|保証します",
    # 生成失敗・プロンプト漏れの痕跡
    r"申し訳ありません|作成できません|できかねます|As an AI|I cannot",
    # ハングル等のグリッチ（音節・字母・互換字母）
    r"[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿]",
)


def validate_post_content(post, *, min_len: int = 20, max_len: int = 500,
                          tree_parts: tuple = (2, 3)) -> str:
    """投稿1本（str または ツリー=list[str]）を検証し、問題なければ "" を、
    問題があれば理由を返す。呼び出し側は理由が返ったらプールに入れない。"""
    import re as _re
    parts = post if isinstance(post, list) else [post]
    if isinstance(post, list) and not (tree_parts[0] <= len(parts) <= tree_parts[1]):
        return f"ツリーのpart数が不正({len(parts)})"
    for p in parts:
        if not isinstance(p, str):
            return f"文字列でない要素({type(p).__name__})"
        s = p.strip()
        if len(s) < min_len:
            return f"短すぎる({len(s)}字)"
        if len(s) > max_len:
            return f"長すぎる({len(s)}字)"
        for pat in NG_CONTENT_PATTERNS:
            m = _re.search(pat, s)
            if m:
                return f"NG表現「{m.group(0)}」"
    return ""


# ── LINE通知 ──────────────────────────────────────────────────
def _default_token() -> str:
    """管理者通知の既定トークン: Claude通知Bot優先 → とうこさんLINEにフォールバック"""
    return os.environ.get("ADMIN_NOTIFY_LINE_TOKEN") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


# とうこさんLINEチャンネルでの管理者(黒木さん)User ID。
# broadcast禁止時のpush宛先。Secret LINE_ADMIN_USER_ID があれば優先し、
# 無い場合のフォールバック値は SAAS_SPEC.md に記載済みの公開情報（トークン無しでは何もできないID）。
_ADMIN_UID_FALLBACK = "Ucf261a250763ff136250262e4639e9ee"


def line_broadcast(text: str, token: str = "", *, timeout: int = 10) -> bool:
    """管理者向けLINE通知。失敗しても例外を投げず、ログを残して False。
    token 未指定時は ADMIN_NOTIFY_LINE_TOKEN → LINE_CHANNEL_ACCESS_TOKEN の順で解決。

    ⚠️ broadcastを使うのは Claude通知Bot（友だち=管理者のみ）の時だけ。
    とうこさんLINE等の顧客が友だちにいるチャンネルでは、broadcastすると
    管理者アラートが顧客全員に配信されてしまうため、管理者への push に自動で切り替える
    （2026-07-12 総点検指摘「broadcast→push化」対応）。"""
    token = token or _default_token()
    if not token:
        print(f"[line] トークン無しのため未送信: {text[:60]}", file=sys.stderr)
        return False
    try:
        admin_bot = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")
        if admin_bot and token == admin_bot:
            # Claude通知Bot: 友だちは管理者1人だけなのでbroadcastで安全
            url = f"{LINE_API}/broadcast"
            payload = {"messages": [{"type": "text", "text": text}]}
        else:
            # 顧客が友だちにいるチャンネル（とうこさんLINE等）: 管理者へ直接push
            uid = os.environ.get("LINE_ADMIN_USER_ID", "") or _ADMIN_UID_FALLBACK
            url = f"{LINE_API}/push"
            payload = {"to": uid, "messages": [{"type": "text", "text": text}]}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout)
        # 障害通知(🚨/⚠️/🔑)を送れた印。workflowの failure() 通知ステップは
        # この印があれば重複送信をスキップする（1障害＝broadcast複数通の浪費を防ぐ）
        try:
            if any(m in text for m in ("🚨", "⚠️", "🔑")):
                with open(".line_notified", "w") as f:
                    f.write("1")
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"::error::[line] 通知送信失敗（処理は継続・通知は届いていない）: {e}", file=sys.stderr)
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
