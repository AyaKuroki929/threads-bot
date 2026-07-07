"""
Threads 公式API 自動投稿スクリプト（Playwright不要版）
Usage: python3 post_api.py morning|noon|evening [--dry-run]

Required env vars (GitHub Secrets → workflow env):
  THREADS_ACCESS_TOKEN  長期アクセストークン（60日有効）
  THREADS_USER_ID       ThreadsユーザーID（数字）

Optional env vars:
  POSTS_FILE, USED_FILE, LAST_RUN_FILE, PRIORITY_FILE
  THREADS_TOPIC
  SKIP_TIME_GUARD=1  時間帯チェックをスキップ（手動補完用）
  SKIP_JITTER=1      遅延なし（テスト用）
"""
import sys
import json
import os
import random
import time
import urllib.request
import urllib.parse
import urllib.error
import subprocess
from datetime import datetime

from botlib import load_json, save_json

_BASE = os.path.dirname(__file__)

POSTS_FILE    = os.environ.get("POSTS_FILE",    os.path.join(_BASE, "posts.json"))
USED_FILE     = os.environ.get("USED_FILE",     os.path.join(_BASE, "used_posts.json"))
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", os.path.join(_BASE, "last_run.json"))
PRIORITY_FILE = os.environ.get("PRIORITY_FILE", os.path.join(_BASE, "priority_posts.json"))
USERNAME      = os.environ.get("THREADS_USERNAME", "")

_INSTAGRAM_CTA_PROB = 0.25
_INSTAGRAM_CTA_BEMOLLE = [
    "\ninstagram.com/bemolle_diet に施術写真を載せています。",
    "\ninstagram.com/bemolle_diet にBeforeAfterを載せています。",
    "\ninstagram.com/bemolle_diet にお客様の声を載せています。",
    "\ninstagram.com/bemolle_diet に施術の様子を載せています。",
    "\ninstagram.com/bemolle_diet にサロンの写真を載せています。",
]
_INSTAGRAM_CTA_PERSONAL_GENERIC = [
    "\ninstagram.com/aya_kuroki_0929 に日々の記録を載せています。",
    "\ninstagram.com/aya_kuroki_0929 に詳しい話を載せています。",
    "\ninstagram.com/aya_kuroki_0929 に続きを書いています。",
]
_INSTAGRAM_CTA_PERSONAL_AUTOMATION = [
    "\ninstagram.com/aya_kuroki_0929 に自動化の仕組みを載せています。",
]
_INSTAGRAM_CTA_PERSONAL_AUTOMATION_WORDS = [
    "自動化", "自動投稿", "仕組み", "システム", "AI", "GH Actions", "スクリプト",
]

def _maybe_add_instagram_cta(texts: list) -> list:
    if random.random() >= _INSTAGRAM_CTA_PROB:
        return texts
    if USERNAME == "bemolle_diet":
        cta = random.choice(_INSTAGRAM_CTA_BEMOLLE)
    else:
        post_body = " ".join(texts)
        has_automation = any(w in post_body for w in _INSTAGRAM_CTA_PERSONAL_AUTOMATION_WORDS)
        pool = _INSTAGRAM_CTA_PERSONAL_AUTOMATION + _INSTAGRAM_CTA_PERSONAL_GENERIC if has_automation \
               else _INSTAGRAM_CTA_PERSONAL_GENERIC
        cta = random.choice(pool)
    result = list(texts)
    candidate = result[-1].rstrip() + cta
    if len(candidate) > 500:
        # CTAを足すと500字上限を超える → CTAは付けない（投稿失敗を防ぐ）
        print(f"[cta] 本文{len(result[-1])}字＋CTAで500字超過のためCTA省略")
        return texts
    result[-1] = candidate
    print(f"[cta] Instagram誘導追加: 「{cta.strip()}」")
    return result

THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID      = os.environ.get("THREADS_USER_ID", "")
THREADS_API = "https://graph.threads.net/v1.0"

EXIT_TOKEN_EXPIRED = 3
EXIT_GENERIC_FAIL  = 2

SLOT_VALID_HOURS = {
    "morning":  (5, 10),
    "noon":     (11, 14),
    "evening":  (17, 23),
}


class TokenExpiredError(Exception):
    pass


# ── 重複投稿防止 ────────────────────────────────────────────

def already_posted_today(time_slot):
    if not os.path.exists(LAST_RUN_FILE):
        return False
    try:
        with open(LAST_RUN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    last = data.get(time_slot)
    if not last:
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    if not last.startswith(today):
        return False
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    h_start, h_end = SLOT_VALID_HOURS.get(time_slot, (0, 24))
    return h_start <= last_dt.hour < h_end


def is_valid_time_for_slot(time_slot):
    if os.environ.get("SKIP_TIME_GUARD") == "1":
        return True
    h_start, h_end = SLOT_VALID_HOURS.get(time_slot, (0, 24))
    return h_start <= datetime.now().hour < h_end


def record_success(time_slot):
    data = load_json(LAST_RUN_FILE, {})
    data[time_slot] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(LAST_RUN_FILE, data)


# ── 投稿選択 ────────────────────────────────────────────────

def _get_priority_indices(time_slot):
    pri = load_json(PRIORITY_FILE, None)
    if pri is None:
        return set()
    indices = set()
    for entry in pri.get(time_slot, []):
        if isinstance(entry, dict):
            i = entry.get("idx")
            if isinstance(i, int):
                indices.add(i)
        elif isinstance(entry, int):
            indices.add(entry)
    return indices


def _consume_priority(time_slot):
    pri = load_json(PRIORITY_FILE, None)
    if pri is None:
        return None, None
    entries = pri.get(time_slot, [])
    if not entries:
        return None, None
    try:
        with open(POSTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        all_posts = data.get(time_slot, [])
    except Exception:
        return None, None
    today_str = datetime.now().strftime("%Y-%m-%d")
    for i, entry in enumerate(entries):
        idx = None
        target_date = today_str
        if isinstance(entry, dict):
            idx = entry.get("idx")
            target_date = entry.get("date", today_str)
        elif isinstance(entry, int):
            idx = entry
        if idx is None or target_date > today_str:
            continue
        if idx < len(all_posts):
            entries.pop(i)
            pri[time_slot] = entries
            save_json(PRIORITY_FILE, pri)
            return idx, all_posts[idx]
    return None, None


def select_post(time_slot):
    p_idx, p_text = _consume_priority(time_slot)
    if p_idx is not None:
        print(f"[priority] 優先投稿 idx={p_idx} を使用")
        return p_idx, p_text

    used = {}
    if os.path.exists(USED_FILE):
        try:
            with open(USED_FILE, encoding="utf-8") as f:
                used = json.load(f)
        except Exception:
            used = {}
    used_set = set(used.get(time_slot, []))

    if not os.path.exists(POSTS_FILE):
        raise RuntimeError(f"投稿ファイルが見つかりません: {POSTS_FILE}")
    with open(POSTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    all_posts = data.get(time_slot, [])
    if not all_posts:
        raise RuntimeError(f"{time_slot} スロットの投稿が posts.json にありません")

    reserved = _get_priority_indices(time_slot)
    available = [i for i in range(len(all_posts)) if i not in used_set and i not in reserved]
    if not available:
        raise RuntimeError(
            f"{time_slot} の未使用投稿が0件です。generate_posts.py を実行してください。"
        )

    idx = random.choice(available)
    print(f"[post] idx={idx} / 未使用{len(available)}件 / 全{len(all_posts)}件")
    return idx, all_posts[idx]


def commit_used(time_slot, idx):
    used = load_json(USED_FILE, {})
    used.setdefault(time_slot, [])
    if idx not in used[time_slot]:
        used[time_slot].append(idx)
    save_json(USED_FILE, used)


# ── Git ────────────────────────────────────────────────────

def _git_pull_latest():
    try:
        r = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            capture_output=True, timeout=30, cwd=_BASE
        )
        if r.returncode == 0:
            print("[guard] git pull完了")
        else:
            print(f"[guard] git pull失敗（続行）: {r.stderr.decode()[:100]}")
    except Exception as e:
        print(f"[guard] git pull例外（続行）: {e}")


# ── Threads API 投稿 ────────────────────────────────────────

def _select_topic(texts, username):
    """投稿内容に最適なトピックをClaude APIで選択して返す。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if username == "bemolle_diet":
        fallback_candidates = ["美容", "ダイエット", "エステ", "スキンケア", "健康"]
        account_hint = "エステサロン・ダイエット・スキンケア・美容"
    else:
        fallback_candidates = ["ビジネス", "起業", "AI", "自動化", "経営"]
        account_hint = "ビジネス・起業・AI・自動化・生産性"

    if not api_key:
        chosen = random.choice(fallback_candidates)
        print(f"[topic] APIキー未設定 → フォールバック: '{chosen}'")
        return chosen

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        post_body = "\n".join(texts)[:600]
        prompt = f"""以下のThreads投稿に最もぴったりなトピック（話題カテゴリ）を1つだけ選んでください。
アカウントのテーマ：{account_hint}

【投稿内容】
{post_body}

【選び方の基準】
・Threads内でそのキーワードで検索したときに関連コンテンツが出るような一般的なカテゴリ名
・日本語1〜3語のキーワード（例：美容、ダイエット、起業、AI、スキンケア）
・投稿の主題を最もよく表すもの

キーワードだけ出力してください。説明・記号・改行は不要です。"""
        import re as _re
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        )
        topic = _re.sub(r'[「」『』・\n\r]', '', resp.content[0].text).strip()
        print(f"[topic] AI選択: '{topic}'")
        return topic
    except Exception as e:
        chosen = random.choice(fallback_candidates)
        print(f"[topic] AI選択失敗 → フォールバック '{chosen}': {e}")
        return chosen


def _api_post(user_id, token, text, reply_to_id=None, topic_tag=None):
    """コンテナ作成 → 待機 → 公開。post_id を返す。"""
    # 500字上限の安全キャップ（どんな生成でも長さ起因で投稿失敗しないように）
    if len(text) > 500:
        _cut = text[:500]
        _b = max(_cut.rfind("。"), _cut.rfind("！"), _cut.rfind("？"), _cut.rfind("\n"))
        text = _cut[:_b + 1] if _b >= 300 else text[:497] + "…"
        print(f"[cap] 投稿が500字超のため{len(text)}字に短縮しました")
    # Step 1: コンテナ作成
    payload = {
        "media_type": "TEXT",
        "text": text,
        "access_token": token,
    }
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    if topic_tag:
        payload["topic_tag"] = topic_tag

    create_url = f"{THREADS_API}/{user_id}/threads"

    def _create_container(pl):
        data = urllib.parse.urlencode(pl).encode()
        req = urllib.request.Request(create_url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())["id"]
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code in (401, 403) or "invalid_token" in body.lower() or "token" in body.lower():
                raise TokenExpiredError(f"トークンエラー HTTP {e.code}: {body[:200]}")
            raise RuntimeError(f"コンテナ作成失敗 HTTP {e.code}: {body[:200]}")

    try:
        creation_id = _create_container(payload)
    except RuntimeError:
        if "topic_tag" in payload:
            print(f"[topic] topic_tag='{payload['topic_tag']}' が拒否された → トピックなしで再試行")
            payload_no_topic = {k: v for k, v in payload.items() if k != "topic_tag"}
            creation_id = _create_container(payload_no_topic)
        else:
            raise

    # Step 2: コンテナ準備完了を待機（API推奨: 30秒）
    time.sleep(30)

    # Step 3: 公開
    publish_url = f"{THREADS_API}/{user_id}/threads_publish"
    pub_data = urllib.parse.urlencode({
        "creation_id": creation_id,
        "access_token": token,
    }).encode()
    req = urllib.request.Request(publish_url, data=pub_data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"公開失敗 HTTP {e.code}: {body[:200]}")


# Meta(Threads)側の一時障害（HTTP 5xx / 429 / is_transient）は数分続くことがある。
# その間は段階的に待ち時間を延ばして粘る（30秒→2分→5分）。待機中にMetaが復旧すれば
# 投稿成功する。通常エラー（400等の恒久エラー）は短い待ちで済ます。
TRANSIENT_RETRY_WAITS = [30, 120, 300]  # 一時障害時の待機秒（失敗回数ごと）
QUICK_RETRY_WAIT = 10                    # その他エラー時の待機秒
MAX_POST_ATTEMPTS = len(TRANSIENT_RETRY_WAITS) + 1  # 合計4回試行


def _is_transient_error(e):
    """Meta側の一時障害（リトライで回復しうる）かどうか。"""
    s = str(e)
    return any(marker in s for marker in (
        "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "HTTP 429", "is_transient"))


def threads_api_post(user_id, token, texts, topic_tag=None):
    """単発またはツリー投稿。texts: list[str]"""
    first_post_id = None
    reply_to_id = None
    for i, text in enumerate(texts):
        post_id = _api_post(user_id, token, text, reply_to_id=reply_to_id,
                            topic_tag=topic_tag)
        if i == 0:
            first_post_id = post_id
        # 次partは「直前のpartへの返信」にする（毎回更新しないと3部目以降が
        # 1部目への兄弟返信になり、ツリーがチェーンにならない）
        reply_to_id = post_id
        print(f"[api] part {i+1}/{len(texts)} 投稿完了: post_id={post_id}")
        if i < len(texts) - 1:
            time.sleep(3)
    return first_post_id


# ── メイン ─────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ["morning", "noon", "evening"]:
        print("使い方: python3 post_api.py morning|noon|evening [--dry-run]")
        sys.exit(1)

    time_slot = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    if not dry_run:
        if not THREADS_ACCESS_TOKEN:
            print("[fatal] THREADS_ACCESS_TOKEN が設定されていません")
            sys.exit(EXIT_GENERIC_FAIL)

    if not dry_run:
        _git_pull_latest()

    # /me でトークンの実際のユーザーIDを取得（設定値と照合してどちらか使う）
    actual_user_id = THREADS_USER_ID
    if not dry_run:
        try:
            me_url = f"{THREADS_API}/me?fields=id,username&access_token={THREADS_ACCESS_TOKEN}"
            req = urllib.request.Request(me_url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                me_data = json.loads(resp.read())
            token_user_id = str(me_data.get("id", ""))
            token_username = me_data.get("username", "")
            print(f"[token] /me → id={token_user_id} username={token_username}")
            if THREADS_USER_ID and token_user_id != THREADS_USER_ID:
                print(f"[warn] THREADS_USER_ID({THREADS_USER_ID}) ≠ token user({token_user_id}). token の user_id を使用します。")
            actual_user_id = token_user_id or THREADS_USER_ID
        except Exception as e:
            print(f"[warn] /me 取得失敗: {e}. THREADS_USER_ID をそのまま使用。")
            actual_user_id = THREADS_USER_ID

    if not actual_user_id:
        print("[fatal] user_id が取得できませんでした（THREADS_USER_ID 未設定かつ /me 失敗）")
        sys.exit(EXIT_GENERIC_FAIL)

    if not dry_run and already_posted_today(time_slot):
        print(f"[skip] {time_slot} は本日すでに投稿済み。終了。")
        return

    if not dry_run and not is_valid_time_for_slot(time_slot):
        h_start, h_end = SLOT_VALID_HOURS.get(time_slot, (0, 24))
        now_hms = datetime.now().strftime('%H:%M')
        print(f"[skip] {now_hms} JST は {time_slot} の正規時間帯（{h_start:02d}:00〜{h_end:02d}:00）外。"
              "手動補完時は SKIP_TIME_GUARD=1 を設定。")
        return

    if dry_run:
        texts = ["テスト投稿（dry-run）。これは実際には投稿されません。"]
        idx = None
    else:
        idx, text = select_post(time_slot)
        texts = [text] if isinstance(text, str) else list(text)
        texts = _maybe_add_instagram_cta(texts)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] {time_slot} 投稿{'（dry-run）' if dry_run else '開始'}")
    print(f"--- 投稿内容 ({len(texts)}部) ---")
    print(texts[0][:200])
    print()

    if dry_run:
        print("[dry-run] 終了（投稿なし）")
        return

    # トピック選択
    topic_tag = _select_topic(texts, USERNAME)

    # ランダム遅延（bot臭消し: 0〜5分）
    if not os.environ.get("SKIP_JITTER"):
        jitter_sec = random.randint(0, 300)
        print(f"[jitter] {jitter_sec}秒待機（{jitter_sec // 60}分{jitter_sec % 60}秒）")
        time.sleep(jitter_sec)

    # 投稿（一時障害は段階的バックオフで粘る: 30秒→2分→5分・計4回）
    last_exc = None
    for attempt in range(1, MAX_POST_ATTEMPTS + 1):
        try:
            post_id = threads_api_post(actual_user_id, THREADS_ACCESS_TOKEN, texts,
                                       topic_tag=topic_tag)
            last_exc = None
            break
        except TokenExpiredError as e:
            print(f"[fatal] トークン切れ: {e}")
            sys.exit(EXIT_TOKEN_EXPIRED)
        except Exception as e:
            last_exc = e
            print(f"[retry] 試行{attempt}/{MAX_POST_ATTEMPTS} 失敗: {e}")
            if attempt < MAX_POST_ATTEMPTS:
                if _is_transient_error(e):
                    wait = TRANSIENT_RETRY_WAITS[attempt - 1]
                    print(f"[retry]   → 一時障害の疑い。{wait}秒待って再試行")
                else:
                    wait = QUICK_RETRY_WAIT
                    print(f"[retry]   → {wait}秒待って再試行")
                time.sleep(wait)

    if last_exc is not None:
        print(f"[fatal] {MAX_POST_ATTEMPTS}回試行して投稿できませんでした: {last_exc}")
        sys.exit(EXIT_GENERIC_FAIL)

    commit_used(time_slot, idx)
    record_success(time_slot)
    print(f"✅ 投稿完了！post_id={post_id}")


if __name__ == "__main__":
    main()
