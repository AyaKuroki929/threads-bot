"""
Threads 公式API 自動投稿スクリプト（Playwright不要版）
Usage: python3 post_api.py morning|noon|evening [--dry-run]

Required env vars (GitHub Secrets → workflow env):
  THREADS_ACCESS_TOKEN  長期アクセストークン（60日有効）
  THREADS_USER_ID       ThreadsユーザーID（数字）

Optional env vars:
  POSTS_FILE, USED_FILE, LAST_RUN_FILE, PRIORITY_FILE
  LINE_CHANNEL_ACCESS_TOKEN, THREADS_TOPIC
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

_BASE = os.path.dirname(__file__)

POSTS_FILE    = os.environ.get("POSTS_FILE",    os.path.join(_BASE, "posts.json"))
USED_FILE     = os.environ.get("USED_FILE",     os.path.join(_BASE, "used_posts.json"))
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", os.path.join(_BASE, "last_run.json"))
PRIORITY_FILE = os.environ.get("PRIORITY_FILE", os.path.join(_BASE, "priority_posts.json"))
LINE_TOKEN    = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
USERNAME      = os.environ.get("THREADS_USERNAME", "")

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
    data = {}
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[time_slot] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 投稿選択 ────────────────────────────────────────────────

def _get_priority_indices(time_slot):
    if not os.path.exists(PRIORITY_FILE):
        return set()
    try:
        with open(PRIORITY_FILE, encoding="utf-8") as f:
            pri = json.load(f)
    except Exception:
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
    if not os.path.exists(PRIORITY_FILE):
        return None, None
    try:
        with open(PRIORITY_FILE, encoding="utf-8") as f:
            pri = json.load(f)
    except Exception:
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
            with open(PRIORITY_FILE, "w", encoding="utf-8") as f:
                json.dump(pri, f, ensure_ascii=False, indent=2)
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
    used = {}
    if os.path.exists(USED_FILE):
        try:
            with open(USED_FILE, encoding="utf-8") as f:
                used = json.load(f)
        except Exception:
            used = {}
    used.setdefault(time_slot, [])
    if idx not in used[time_slot]:
        used[time_slot].append(idx)
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used, f, ensure_ascii=False, indent=2)


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

def _api_post(user_id, token, text, reply_to_id=None):
    """コンテナ作成 → 待機 → 公開。post_id を返す。"""
    # Step 1: コンテナ作成
    payload = {
        "media_type": "TEXT",
        "text": text,
        "access_token": token,
    }
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id

    create_url = f"{THREADS_API}/{user_id}/threads"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(create_url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            creation_id = json.loads(resp.read())["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code in (401, 403) or "invalid_token" in body.lower() or "token" in body.lower():
            raise TokenExpiredError(f"トークンエラー HTTP {e.code}: {body[:200]}")
        raise RuntimeError(f"コンテナ作成失敗 HTTP {e.code}: {body[:200]}")

    # Step 2: コンテナ準備完了を待機（API推奨: 30秒）
    time.sleep(30)

    # Step 3: 公開
    publish_url = f"{THREADS_API}/{user_id}/threads/publish"
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


def threads_api_post(user_id, token, texts):
    """単発またはツリー投稿。texts: list[str]"""
    first_post_id = None
    reply_to_id = None
    for i, text in enumerate(texts):
        post_id = _api_post(user_id, token, text, reply_to_id=reply_to_id)
        if i == 0:
            first_post_id = post_id
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
        if not THREADS_USER_ID:
            print("[fatal] THREADS_USER_ID が設定されていません")
            sys.exit(EXIT_GENERIC_FAIL)

    if not dry_run:
        _git_pull_latest()

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

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] {time_slot} 投稿{'（dry-run）' if dry_run else '開始'}")
    print(f"--- 投稿内容 ({len(texts)}部) ---")
    print(texts[0][:200])
    print()

    if dry_run:
        print("[dry-run] 終了（投稿なし）")
        return

    # ランダム遅延（bot臭消し: 0〜5分）
    if not os.environ.get("SKIP_JITTER"):
        jitter_sec = random.randint(0, 300)
        print(f"[jitter] {jitter_sec}秒待機（{jitter_sec // 60}分{jitter_sec % 60}秒）")
        time.sleep(jitter_sec)

    # 投稿（最大3回リトライ）
    last_exc = None
    for attempt in range(1, 4):
        try:
            post_id = threads_api_post(THREADS_USER_ID, THREADS_ACCESS_TOKEN, texts)
            last_exc = None
            break
        except TokenExpiredError as e:
            print(f"[fatal] トークン切れ: {e}")
            sys.exit(EXIT_TOKEN_EXPIRED)
        except Exception as e:
            last_exc = e
            print(f"[retry] 試行{attempt}/3 失敗: {e}")
            if attempt < 3:
                time.sleep(60)

    if last_exc is not None:
        print(f"[fatal] 3回試行して投稿できませんでした: {last_exc}")
        sys.exit(EXIT_GENERIC_FAIL)

    commit_used(time_slot, idx)
    record_success(time_slot)
    print(f"✅ 投稿完了！post_id={post_id}")


if __name__ == "__main__":
    main()
