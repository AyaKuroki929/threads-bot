"""
Threadsに自動投稿するスクリプト（self-reply chain でツリー投稿）
使い方: python3 post.py morning  /  python3 post.py noon  /  python3 post.py evening
"""
import sys
import json
import random
import os
import re
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

_BASE = os.path.dirname(__file__)
POSTS_FILE    = os.environ.get("POSTS_FILE",    os.path.join(_BASE, "posts.json"))
SESSION_FILE  = os.environ.get("SESSION_FILE",  os.path.join(_BASE, "session.json"))
USED_FILE     = os.environ.get("USED_FILE",     os.path.join(_BASE, "used_posts.json"))
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", os.path.join(_BASE, "last_run.json"))
PRIORITY_FILE = os.environ.get("PRIORITY_FILE", os.path.join(_BASE, "priority_posts.json"))
USERNAME      = os.environ.get("THREADS_USERNAME", "bemolle_diet")
COMMENT_TARGETS_FILE = os.environ.get("COMMENT_TARGETS_FILE", os.path.join(_BASE, "comment_targets.json"))
COMMENTED_FILE = os.environ.get("COMMENTED_FILE", os.path.join(_BASE, "commented_posts.json"))
AUTO_COMMENT  = os.environ.get("AUTO_COMMENT", "") == "1"
# 1回の実行でコメントするアカウント数の上限（0=全件）
MAX_COMMENTS_PER_RUN = int(os.environ.get("MAX_COMMENTS_PER_RUN", "6"))
# プール内の未コメントアカウントがこの数を下回ったら自動発掘を実行
COMMENT_MIN_POOL = int(os.environ.get("COMMENT_MIN_POOL", "30"))
# 1回の自動発掘で追加する最大アカウント数
COMMENT_DISCOVER_MAX = int(os.environ.get("COMMENT_DISCOVER_MAX", "50"))
# コメントクールダウン日数（同一アカウント・同一投稿への再コメント禁止期間）
COMMENT_COOLDOWN_DAYS = int(os.environ.get("COMMENT_COOLDOWN_DAYS", "7"))
# 自動発掘の検索キーワードファイル（JSON配列）
COMMENT_KEYWORDS_FILE = os.environ.get("COMMENT_KEYWORDS_FILE", os.path.join(_BASE, "comment_search_keywords.json"))
LINE_TOKEN    = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
TOPIC         = os.environ.get("THREADS_TOPIC", "")

# 投稿失敗時の終了コード
EXIT_COOKIE_EXPIRED = 3   # Threadsログイン切れ → workflow側で専用Issue作成
EXIT_GENERIC_FAIL = 2


class CookieExpiredError(Exception):
    """Threadsのcookieが切れている時に送出。"""
    pass


def _detect_login_required(page):
    """ログイン画面に飛ばされていないかチェック。飛ばされていたら CookieExpiredError。
    a[href*="/login?"] は複数アカウントページにも存在するため使わない。
    URLにloginが含まれるか、ログインフォーム入力欄が表示されているかで判定。
    パスワード変更後に出る "Say more with Threads" 再認証モーダルも検出する。"""
    if "login" in page.url.lower():
        raise CookieExpiredError(
            f"Threadsのログインセッションが切れています（URL: {page.url}）。"
            "Macで `python3 extract_cookies2.py && gh secret set THREADS_SESSION < session.json` を実行してください。"
        )
    login_form_selectors = [
        'input[autocomplete="username"]',
        'input[name="username"]',
    ]
    for sel in login_form_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                raise CookieExpiredError(
                    f"Threadsのログインセッションが切れています（{sel} 検出）。"
                    "Macで `python3 extract_cookies2.py && gh secret set THREADS_SESSION < session.json` を実行してください。"
                )
        except CookieExpiredError:
            raise
        except Exception:
            continue
    # パスワード変更後のセッション半壊状態で出る再認証モーダルを検出
    reauth_selectors = [
        'button:has-text("Continue with Instagram")',
        'a:has-text("Continue with Instagram")',
        'div[role="button"]:has-text("Continue with Instagram")',
    ]
    for sel in reauth_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                raise CookieExpiredError(
                    "Threads再認証モーダルが表示されました（セッション半壊）。"
                    "Chrome Profile でThreadsに再ログイン後、cookie を再取得してください。"
                )
        except CookieExpiredError:
            raise
        except Exception:
            continue


def _verify_account(page):
    """ログイン中アカウントが USERNAME と一致するか確認。不一致なら即 SystemExit。
    投稿前に必ず呼ぶ。誤アカウントへの投稿を防ぐ最終防波堤。"""
    # ① Cookie の ds_user_id で確認（UI変更に左右されない最優先チェック）
    expected_uid = os.environ.get("EXPECTED_USER_ID", "")
    if expected_uid:
        try:
            cookies = page.context.cookies()
            actual_uid = next(
                (c["value"] for c in cookies
                 if c["name"] == "ds_user_id" and "threads" in c.get("domain", "")),
                ""
            )
            if actual_uid == expected_uid:
                print(f"[account] ✅ @{USERNAME} でログイン確認済み（ds_user_id: {actual_uid}）")
                return
            if actual_uid:
                print(f"[account] ❌ アカウント不一致（cookie）。期待UID={expected_uid} / 実際={actual_uid}")
                sys.exit(4)
            # actual_uid が空の場合は ② へフォールスルー
        except SystemExit:
            raise
        except Exception as e:
            print(f"[account] ⚠️ Cookie確認失敗、UIチェックにフォールバック: {e}")

    # ② ブラウザUIで確認（ds_user_id が取れなかった場合のフォールバック）
    try:
        page.goto(f"https://www.threads.com/@{USERNAME}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        _detect_login_required(page)
        edit_selectors = [
            '[aria-label*="プロフィールを編集"]',
            '[aria-label*="Edit profile"]',
            'a[href*="edit_profile"]',
            'div[role="button"]:has-text("プロフィールを編集")',
            'div[role="button"]:has-text("Edit profile")',
        ]
        for sel in edit_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0:
                print(f"[account] ✅ @{USERNAME} でログイン確認済み（edit button）")
                return
        actual_url = page.url
        print(f"[account] ❌ アカウント不一致。期待: @{USERNAME} / URL: {actual_url}")
        print(f"[account] THREADS_SESSION シークレットが @{USERNAME} のセッションか確認してください。")
        sys.exit(4)
    except SystemExit:
        raise
    except CookieExpiredError:
        raise
    except Exception as e:
        print(f"[account] ⚠️ アカウント確認スキップ（確認不能）: {e}")


def already_posted_today(time_slot):
    """今日その時間帯に投稿済みか。cron多重発火時の重複防止。
    各slotには「妥当な投稿時間帯」を設定し、その範囲内の記録のみ「投稿済み」と判定する。
    範囲外（例: evening が深夜0時に記録）は遅延発火扱いで無効 → 正規時間帯に再発火させる。

    JST時間帯：
    - morning: 5:00〜9:59
    - noon:    11:00〜13:59
    - evening: 17:00〜22:59
    """
    if not os.path.exists(LAST_RUN_FILE):
        return False
    try:
        with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
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

    valid_hours = {
        "morning":  (5, 10),
        "morning2": (8, 11),
        "noon":     (11, 14),
        "evening2": (21, 24),
        "evening":  (17, 23),
    }
    h_start, h_end = valid_hours.get(time_slot, (0, 24))
    return h_start <= last_dt.hour < h_end


# slotごとの「現在時刻が投稿していい時間帯か」判定用（同じテーブル）
SLOT_VALID_HOURS = {
    "morning":  (5, 10),
    "morning2": (8, 11),
    "noon":     (11, 14),
    "evening2": (21, 24),
    "evening":  (17, 23),
}


def is_valid_time_for_slot(time_slot):
    """現在時刻(JST)が指定slotの正規時間帯内か。
    範囲外なら投稿しない（heartbeat遅延発火・意図しないトリガから守る）。
    SKIP_TIME_GUARD=1 で無効化（手動補完時用のescape hatch）。"""
    if os.environ.get("SKIP_TIME_GUARD") == "1":
        return True
    h_start, h_end = SLOT_VALID_HOURS.get(time_slot, (0, 24))
    now_hour = datetime.now().hour
    return h_start <= now_hour < h_end


def record_success(time_slot):
    data = {}
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[time_slot] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def schedule_next_wake(time_slot):
    """次の投稿時刻に Mac を自動 wake させる予約。Mac実行時のみ動く。
    クラウド（GitHub Actions等）実行時は CI 環境変数で検知してスキップする。"""
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return

    next_wake_times = {
        "morning":  ["08:58:00", "09:06:00"],
        "morning2": ["11:58:00", "12:06:00"],
        "noon":     ["17:58:00", "18:06:00"],
        "evening2": ["20:58:00", "21:06:00"],
    }
    if time_slot not in next_wake_times:
        return

    target_date = datetime.now().strftime("%m/%d/%y")
    for target_time in next_wake_times[time_slot]:
        schedule_str = f"{target_date} {target_time}"
        try:
            subprocess.run(
                ["sudo", "-n", "/usr/bin/pmset", "schedule", "wakeorpoweron", schedule_str],
                check=True, capture_output=True, text=True, timeout=10
            )
            print(f"[wake予約] {schedule_str} 登録完了")
        except subprocess.CalledProcessError as e:
            print(f"[warn] wake予約失敗 ({schedule_str}): {e.stderr.strip() if e.stderr else e}")
        except Exception as e:
            print(f"[warn] wake予約で例外 ({schedule_str}): {e}")


def wait_for_network(timeout=240, interval=5):
    """ネットが繋がるまで待機。DarkWake直後のWi-Fi未接続対策。"""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                "https://www.threads.com/",
                method="HEAD",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status < 500:
                    print(f"[net] 疎通OK status={resp.status}")
                    return True
        except Exception as e:
            last_err = e
        time.sleep(interval)
    print(f"[net] タイムアウト ({timeout}s) 最後のエラー: {last_err}")
    return False


def _get_priority_indices(time_slot):
    """priority_posts.json でこの slot にキューされている全 idx を返す（過去日付含む）。
    select_post の random 選択で priority 予約済 idx を除外するために使う。"""
    if not os.path.exists(PRIORITY_FILE):
        return set()
    try:
        with open(PRIORITY_FILE, "r", encoding="utf-8") as f:
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
    """priority_posts.json にこのslotで予約されているidxがあれば取り出して返す。
    形式（2種サポート）:
      A) {"morning":[idx, ...], "noon":[idx, ...], "evening":[idx, ...]}
         → FIFO（先頭idxを使用）
      B) {"morning":[{"date":"YYYY-MM-DD","idx":N}, ...], "noon":[...], "evening":[...]}
         → 日付一致のみ消費（過去日付エントリは自動削除）
    既に used_posts.json にある idx は重複防止のため自動スキップ（queueから削除）。
    None を返した場合は通常のランダム選択にfallback。"""
    if not os.path.exists(PRIORITY_FILE):
        return None
    try:
        with open(PRIORITY_FILE, "r", encoding="utf-8") as f:
            pri = json.load(f)
    except Exception:
        return None
    queue = pri.get(time_slot, [])
    if not queue:
        return None

    # used_posts.json を読み込み、重複idxを検出
    used = {}
    if os.path.exists(USED_FILE):
        try:
            with open(USED_FILE, "r", encoding="utf-8") as f:
                used = json.load(f)
        except Exception:
            pass
    used_indices = set(used.get(time_slot, []))

    today = datetime.now().strftime("%Y-%m-%d")
    chosen_idx = None
    new_queue = []

    for entry in queue:
        if isinstance(entry, dict):
            # 日付指定形式
            d = entry.get("date")
            i = entry.get("idx")
            if d == today and chosen_idx is None:
                # 既に投稿済みidxならスキップ（重複防止）
                if i in used_indices:
                    print(f"[priority] idx={i} は既にused_posts.jsonにある → 重複防止のためスキップ、通常選択にfallback")
                    continue
                chosen_idx = i
                continue   # 今日消費 → queueから外す
            elif d and d < today:
                # 期限切れ → 自動削除（queueに残さない）
                print(f"[priority] expired entry removed: {entry}")
                continue
            else:
                new_queue.append(entry)
        else:
            # 旧FIFO形式（int idx）
            if chosen_idx is None:
                if entry in used_indices:
                    print(f"[priority] idx={entry} は既にused_posts.jsonにある → 重複防止のためスキップ")
                    continue
                chosen_idx = entry
                continue
            new_queue.append(entry)

    if chosen_idx is not None:
        pri[time_slot] = new_queue
        with open(PRIORITY_FILE, "w", encoding="utf-8") as f:
            json.dump(pri, f, ensure_ascii=False, indent=2)
        print(f"[priority] {time_slot} の予約投稿 idx={chosen_idx} を選択")
        return chosen_idx
    return None


def select_post(time_slot):
    """ネタを1つ選んで (idx, text_or_list) を返す。used_posts.json への書き込みはしない。
    まず priority_posts.json の予約をチェックし、あればそれを使う。
    無ければ used_indices を除いた未使用プールからランダム。
    全消費した場合は RuntimeError で停止する（CI失敗→メール通知で気付ける）。"""
    with open(POSTS_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)

    # 予約優先
    priority_idx = _consume_priority(time_slot)
    if priority_idx is not None and 0 <= priority_idx < len(posts[time_slot]):
        return priority_idx, posts[time_slot][priority_idx]

    used = {}
    if os.path.exists(USED_FILE):
        with open(USED_FILE, "r", encoding="utf-8") as f:
            used = json.load(f)

    used_indices = used.get(time_slot, [])
    all_posts = posts[time_slot]
    # 将来予約されているidxはランダム選択から除外（priority idxを勝手に消費しない）
    priority_reserved = _get_priority_indices(time_slot)
    available = [i for i in range(len(all_posts))
                 if i not in used_indices and i not in priority_reserved]
    if not available:
        # 予約除外で空になる場合は予約も含めて再選択（壊滅回避）
        available = [i for i in range(len(all_posts)) if i not in used_indices]
        if available:
            print(f"[warn] {time_slot} priority予約以外に未使用が無いため、予約idxも候補に含めます")
    if not available:
        raise RuntimeError(
            f"[ネタ枯渇] {time_slot} の未使用ネタが0本です。Macで regen.sh を手動実行して "
            f"posts.json を補充してください: cd ~/threads_bot && ./regen.sh"
        )

    idx = random.choice(available)
    return idx, all_posts[idx]


def commit_used(time_slot, idx):
    """投稿成功後に used_posts.json へコミット。リセットせず永続的に記録し続ける。
    posts.json は regen.sh で追記され続けるので、indices は単調増加していく。
    一度使ったidxは二度と引かれない＝過去投稿との被りを永続的にゼロ保証。"""
    used = {}
    if os.path.exists(USED_FILE):
        with open(USED_FILE, "r", encoding="utf-8") as f:
            used = json.load(f)
    used.setdefault(time_slot, [])
    if idx not in used[time_slot]:
        used[time_slot].append(idx)
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used, f, ensure_ascii=False, indent=2)


def _input_text(page, text):
    """モーダル内の最初のcontenteditableに改行込みテキストをkeyboard.typeで入力。"""
    textarea = page.locator('[contenteditable="true"]').first
    textarea.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    try:
        textarea.click(timeout=5000)
    except Exception:
        # オーバーレイに遮られた場合は force click で突破
        textarea.click(force=True)
    textarea.focus()  # force click後もキーボードフォーカスを確実に当てる
    page.wait_for_timeout(600)
    # 既存テキストをクリア（前回の残留テキスト対策）
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.wait_for_timeout(200)
    for i, line in enumerate(text.split("\n")):
        if i > 0:
            page.keyboard.press("Shift+Enter")
        if line:
            page.keyboard.type(line, delay=25)
    page.wait_for_timeout(800)


def _click_submit(page):
    """ダイアログ内の「投稿」ボタンを掴んでclickし、ダイアログが閉じるまで待つ。"""
    dialog = page.locator('div[role="dialog"]').last
    all_buttons = dialog.locator('div[role="button"]')
    btn_count = all_buttons.count()
    EXCLUDE = {"スレッドに追加", "Add to thread", "キャンセル", "Cancel", "オプション", "Options"}
    SUBMIT_LABELS = {"投稿", "投稿する", "Post"}
    candidates = []
    for i in range(btn_count):
        try:
            txt = all_buttons.nth(i).inner_text().strip()
        except Exception:
            continue
        if txt in EXCLUDE:
            continue
        if txt in SUBMIT_LABELS:
            candidates.append((i, txt))
    if not candidates:
        for i in range(btn_count):
            try:
                txt = all_buttons.nth(i).inner_text().strip()
            except Exception:
                continue
            if txt in EXCLUDE:
                continue
            if "投稿" in txt or "Post" in txt:
                candidates.append((i, txt))
    if not candidates:
        raise RuntimeError("送信ボタンが見つかりませんでした")
    target_idx, target_text = candidates[-1]
    target = all_buttons.nth(target_idx)
    print(f"[debug] 送信ボタンクリック: idx={target_idx} text={repr(target_text)}")
    target.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    try:
        target.click(timeout=5000)
    except Exception:
        print("[debug] 送信ボタン通常クリック失敗 → force=Trueで再試行")
        target.click(force=True)
    for _ in range(30):
        page.wait_for_timeout(500)
        if page.locator('div[role="dialog"]').count() == 0:
            break


def _set_topic(page, topic: str):
    """コンポーザーモーダル内でトピックを設定する。失敗しても投稿は続行。
    input[placeholder="トピックを追加"] を直接操作する（ボタン経由は不要）。"""
    if not topic:
        return
    try:
        topic_input = page.locator('input[placeholder="トピックを追加"]').first
        if topic_input.count() == 0:
            topic_input = page.locator(
                'input[placeholder*="トピック"], input[placeholder*="コミュニティ"], input[placeholder*="topic"]'
            ).first
        if topic_input.count() == 0:
            print("[topic] 入力フォームが見つかりません。トピックなしで続行。")
            return
        topic_input.click(force=True)
        page.wait_for_timeout(500)
        topic_input.fill(topic)
        page.wait_for_timeout(1200)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)
        print(f"[topic] '{topic}' を設定しました。")
    except Exception as e:
        print(f"[topic] 設定失敗（続行）: {e}")


def _load_commented():
    """コメント済みログ読み込み。ローカルファイルとgit origin/mainを union merge して返す。
    同一投稿への二重コメントを防ぐため、必ず両方を合わせた最大セットを使用する。"""
    import subprocess as _sp
    local: dict = {}
    if os.path.exists(COMMENTED_FILE):
        try:
            with open(COMMENTED_FILE, "r", encoding="utf-8") as f:
                local = json.load(f)
        except Exception:
            local = {}

    # git origin/main の最新版も取得してunion merge
    try:
        r = _sp.run(
            ["git", "show", f"origin/main:{COMMENTED_FILE}"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            remote = json.loads(r.stdout)
            merged = {**remote, **local}  # local優先だが両方のキーを保持
            if len(merged) > len(local):
                print(f"[commented] remote={len(remote)} local={len(local)} merged={len(merged)} (union)")
                # ローカルファイルも最新にしておく
                with open(COMMENTED_FILE, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                return merged
    except Exception:
        pass

    return local


def _save_commented(data):
    with open(COMMENTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _check_account_exists(page, account):
    """アカウントが実在し、投稿が1件以上あるか確認する。
    存在しない・投稿0件・タイムアウトはすべて False を返す。"""
    try:
        page.goto(f"https://www.threads.com/@{account}",
                  wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        url = page.url
        # 404 や not-found にリダイレクトされたら存在しない
        if "404" in url or "not-found" in url or "login" in url.lower():
            return False
        # 投稿が1件以上あれば実在判定
        for sel in ('div[data-pressable-container="true"]', 'article'):
            if page.locator(sel).count() > 0:
                return True
        return False
    except Exception:
        return False


def _already_commented_recently(commented, account, days=COMMENT_COOLDOWN_DAYS):
    """そのアカウントに過去 days 日以内にコメント済みか。
    days 日を超えていれば再コメント可能（プール枯渇防止）。"""
    keys = [k for k in commented.keys() if f"/{account}/" in k]
    if not keys:
        return False
    try:
        latest = max(datetime.strptime(commented[k], "%Y-%m-%d %H:%M:%S") for k in keys)
        return (datetime.now() - latest).days < days
    except Exception:
        return True


def _discover_new_accounts(page, already_done_accounts, max_new=20):
    """Threads検索で新しいアカウントを自動発掘する。
    already_done_accounts: コメント済み or 既にプール内のアカウント名セット
    戻り値: [{"account": ..., "axis": "自動発見", "note": ...}, ...]
    """
    if not os.path.exists(COMMENT_KEYWORDS_FILE):
        print("[discover] キーワードファイルなし → スキップ")
        return []
    try:
        with open(COMMENT_KEYWORDS_FILE, encoding="utf-8") as f:
            keywords = json.load(f)
    except Exception as e:
        print(f"[discover] キーワード読み込み失敗: {e}")
        return []

    discovered = []
    seen = set(already_done_accounts)
    kw_list = keywords[:]
    random.shuffle(kw_list)

    for keyword in kw_list:
        if len(discovered) >= max_new:
            break
        try:
            search_url = f"https://www.threads.com/search?q={urllib.parse.quote(keyword)}&serp_type=default"
            print(f"[discover] 検索中: {keyword}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(4000)

            links = page.eval_on_selector_all(
                'a[href*="/post/"]',
                'els => els.map(e => e.href)'
            )
            candidates = []
            for link in links:
                m = re.search(r'threads\.com/@([^/]+)/post/', link)
                if not m:
                    continue
                account = m.group(1)
                if account not in seen and account != USERNAME:
                    seen.add(account)
                    candidates.append(account)

            # 実在確認してから追加
            for account in candidates:
                if len(discovered) >= max_new:
                    break
                if _check_account_exists(page, account):
                    print(f"[discover] @{account} 実在確認 ✅ → プール追加")
                    discovered.append({
                        "account": account,
                        "axis": "自動発見",
                        "note": f"検索キーワード: {keyword}",
                        "verified": True
                    })
                else:
                    print(f"[discover] @{account} 存在しない/投稿なし → スキップ")

            page.wait_for_timeout(random.randint(2000, 4000))
        except Exception as e:
            print(f"[discover] 検索失敗 ({keyword}): {e}")

    print(f"[discover] 新規アカウント {len(discovered)} 件発見")
    return discovered


def _get_target_latest_post(page, account):
    """@account の最新投稿のURLとテキストを取得。取得できなければ (None, '') を返す。"""
    try:
        page.goto(f"https://www.threads.com/@{account}", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        articles = page.locator('div[data-pressable-container="true"]')
        if articles.count() == 0:
            articles = page.locator('article')
        if articles.count() == 0:
            return None, ""
        first = articles.first
        # URL取得
        time_link = first.locator('time').first
        if time_link.count() == 0:
            return None, ""
        href = time_link.evaluate("el => el.closest('a')?.href")
        if not href or "/post/" not in href:
            return None, ""
        # テキスト取得（投稿本文を抜き出す）
        post_text = ""
        try:
            # span / div 内のテキストを収集（time要素を除く）
            text_nodes = first.locator('span, div[dir="auto"]')
            texts = []
            for i in range(min(text_nodes.count(), 20)):
                t = text_nodes.nth(i).inner_text().strip()
                if t and len(t) > 5 and t not in texts:
                    texts.append(t)
            post_text = " ".join(texts[:6])[:400]
        except Exception:
            pass
        return href, post_text
    except Exception as e:
        print(f"[comment] @{account} 最新投稿取得失敗: {e}")
        return None, ""


# この投稿トピックへのコメントはブランドイメージ保護のため一切しない
_NEGATIVE_IMPRESSION_SKIP_WORDS = [
    # 体臭・においの問題
    "臭い", "臭う", "におい", "ニオイ", "頭皮臭", "体臭", "口臭", "腋臭",
    "わきが", "ワキガ", "加齢臭", "足臭", "足の臭", "汗臭",
    # 衛生・皮膚問題
    "水虫", "たむし",
    # 消化器系
    "便秘", "下痢", "おなら", "放屁",
    # 失禁
    "尿漏れ", "失禁",
    # 経済的困窮
    "生活保護", "自己破産", "多重債務",
]

_ILLNESS_DEATH_WORDS = [
    "病気", "闘病", "余命", "末期", "入院中", "手術前", "手術後",
    "亡くなった", "亡くなりました", "他界", "逝去", "訃報", "ご逝去",
    "ホスピス", "緩和ケア",
    # メンタルヘルス
    "うつ", "鬱", "自傷", "死にたい", "消えたい", "希死", "自殺",
    "パニック障害", "適応障害", "摂食障害", "過労死", "燃え尽きた",
    "メンタルがやばい", "精神科", "心療内科",
]

_QUESTION_SKIP_WORDS = [
    "教えてください", "教えて下さい", "おすすめを教えて", "おすすめはありますか",
    "おすすめありますか", "何がいいですか", "どれがいいですか",
    "どうしたらいいですか", "どうすればいいですか", "アドバイスください",
    "アドバイスお願い", "ご存知の方", "知っている方", "教えてほしい",
    "ご意見ください", "ご意見お待ちしています", "コメントで教えて",
]

_ENTERTAINMENT_SKIP_WORDS = [
    "コンサート", "ライブ会場", "推し活", "ファンクラブ", "アイドル",
    "野球観戦", "サッカー観戦", "スタジアム", "ゲーム実況", "攻略",
    "競馬", "パチンコ", "スロット",
]


# Instagram誘導CTAの追加確率（約1/4）
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
    "自動化", "AI", "仕組み", "効率", "Notion", "LINE", "ツール", "プログラム",
    "スクリプト", "システム", "作業", "時短", "業務",
]


def _maybe_add_instagram_cta(texts: list) -> list:
    """1/4の確率でInstagram誘導CTAを末尾テキストに1行追加。"""
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
    result[-1] = result[-1].rstrip() + cta
    print(f"[cta] Instagram誘導追加: 「{cta.strip()}」")
    return result


_SENTENCE_ENDINGS = ("。", "！", "？", "…", "♪", "〜", "ね", "よ", "わ", "な", "か")

def _trim_to_complete_sentence(text: str) -> str:
    """文章が途中で切れていたら、最後の文末記号で切り捨てる。"""
    text = text.strip()
    if not text:
        return text
    # 既に文末で終わっていればそのまま
    if text[-1] in "。！？…♪〜」』）":
        return text
    # 最後の文末記号を探して、そこで切る
    for end_char in ("。", "！", "？", "…"):
        idx = text.rfind(end_char)
        if idx != -1:
            return text[:idx + 1]
    # 文末記号がなければそのまま返す（短い文の可能性）
    return text


_BOT_REVEAL_PATTERNS = [
    "投稿内容が表示", "表示されていない", "お知らせください", "コメントを書くことができません",
    "投稿のテキスト", "投稿内容を", "内容が見えません", "テキストを教えて",
    "投稿が表示", "確認できません", "内容が確認",
]

def _generate_comment(post_text: str, account_note: str):
    """投稿内容に合ったコメントをClaude APIで生成。生成できない場合はNoneを返す。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not post_text.strip():
        return None

    if USERNAME == "bemolle_diet":
        persona = "大阪でエステサロンを経営している、美容と健康に詳しい30代後半の女性"
    else:
        persona = "大阪でエステサロンを経営している、業務効率化と生産性向上に取り組む30代後半の女性"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        if USERNAME == "bemolle_diet":
            system_prompt = (
                f"あなたは{persona}です。"
                "Threadsでコメントするとき、自分の言葉で短く反応します。"
                "言葉遣いは常にです・ます調。タメ口は絶対に使わない。"
                "AIが書いたと思われるような文章は絶対に書かない。"
                "【重要】一人称は必ず「私」。「うち」「うちの」は絶対に使わない。"
                "【重要】投稿の内容を否定・批判・疑問視するコメントは絶対に書かない。共感・肯定・興味を示すコメントに徹する。"
                "【重要】スキンケア成分・美容成分の組み合わせや使い方について、推奨・否定・アドバイスを一切しない。"
                "成分（ビタミンC・レチノール・ナイアシンアミド・AHA・BHAなど）に言及するときは、推薦でも批判でもなく、共感や感想にとどめる。"
                "サロンオーナーとして軽率なコメントをしない。"
            )
        else:
            system_prompt = (
                f"あなたは{persona}です。"
                "Threadsでコメントするとき、自分の言葉で短く反応します。"
                "言葉遣いは常にです・ます調。タメ口は絶対に使わない。"
                "AIが書いたと思われるような文章は絶対に書かない。"
                "【重要】一人称は必ず「私」。「うち」「うちの」は絶対に使わない。"
                "【重要】投稿の内容を否定・批判・疑問視するコメントは絶対に書かない。共感・肯定・興味を示すコメントに徹する。"
            )

        ingredient_rule = (
            "・スキンケア成分（ビタミンC・レチノール・ナイアシンアミド・AHA・BHAなど）の\n"
            "　組み合わせや使い方を推奨・否定・アドバイスするコメントは絶対に書かない\n"
            "・成分に関する投稿には、共感や「気になります」程度の感想にとどめる\n"
        ) if USERNAME == "bemolle_diet" else ""

        skip_rule = (
            "・性的・暴力的・差別的・スパム目的の投稿には「SKIP」とだけ出力する\n"
            "・日常生活・仕事・食事・家族・趣味など一般の投稿はSKIPしない（テーマと関係なくてもOK）"
        )

        # 3回に1回かつ投稿内容が職業と親和性が高いとき、立場をさりげなく滲ませる
        # 親和性チェック: 美容・健康・ダイエット・仕事・経営・効率化・生活習慣に関わる投稿のみ
        _IDENTITY_HINT_WORDS_BEMOLLE = [
            "美容", "エステ", "スキンケア", "ダイエット", "痩せ", "肌", "体型", "むくみ",
            "セルライト", "たるみ", "フェイシャル", "コスメ", "化粧", "健康", "食事",
            "生活習慣", "睡眠", "体重", "腸", "サロン", "自分磨き", "ケア",
        ]
        _IDENTITY_HINT_WORDS_PERSONAL = [
            "サロン", "エステ", "美容", "経営", "起業", "集客", "売上", "自動化", "AI",
            "業務", "効率", "仕組み", "一人", "副業", "スタッフ", "予約", "LINE", "SNS",
            "Notion", "Instagram", "マーケ", "集客", "事務", "管理",
        ]
        _r = random.random()
        if USERNAME == "bemolle_diet":
            _hint_words = _IDENTITY_HINT_WORDS_BEMOLLE
            _hint_text = (
                "・コメントの中に「大阪でエステをやっているので」「サロンを経営していると」「エステの仕事をしていると」"
                "のような自分の立場を1語だけ自然に入れる（例：「大阪でエステをやってるのでこれわかります。」）\n"
            )
        else:
            _hint_words = _IDENTITY_HINT_WORDS_PERSONAL
            _hint_text = (
                "・コメントの中に「サロン経営してるので」「一人でサロンをやっていると」「エステをやりながらAIも使っているので」"
                "のような自分の立場を1語だけ自然に入れる（例：「サロン経営してるのでまさにこれです。」）\n"
            )
        _post_is_relevant = any(w in post_text for w in _hint_words)
        identity_hint = _hint_text if (_r < 0.33 and _post_is_relevant) else ""

        user_prompt = f"""この投稿を読んで、コメントを1文だけ書いてください。

{post_text[:300]}

---
絶対に守ること：
{skip_rule}
・必ず文章を完結させる。「〜と感」「〜ので」など途中で終わらない
・1〜2文で完結。40文字以内が理想。どんなに長くても55文字まで
・です・ます調（敬語）で書く
・投稿全体の意図・トーンを読んで反応する（疑問文なら「気になりますよね」、体験談なら感想、主張なら共感など）
・投稿の内容を事実として断定・言い換えるコメントは絶対に書かない。必ず感想・疑問・共感にとどめる
・「参考になります」「勉強になりました」「素晴らしいですね」「すごいですね」は使わない
・「とても」「非常に」「大変」などの強調語は使わない
・定型文・テンプレっぽい言い回しは使わない
{identity_hint}・サロン名や具体的な宣伝は書かない
・ハッシュタグなし、絵文字は使わない
・一人称は「私」のみ。「うち」「うちの」は絶対に使わない
・投稿の内容を否定・批判・「難しい」「疑問」などネガティブに捉えるコメントは絶対に書かない
{ingredient_rule}
コメント本文だけ出力してください。他の文字は一切不要。"""

        resp = None
        for _attempt in range(3):
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=80,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}]
                )
                break
            except Exception as _e:
                if "529" in str(_e) or "overloaded" in str(_e).lower():
                    import time as _time
                    print(f"[comment] Anthropic過負荷 → {10 * (_attempt + 1)}秒後リトライ ({_attempt + 1}/3)")
                    _time.sleep(10 * (_attempt + 1))
                else:
                    raise
        if resp is None:
            raise RuntimeError("Anthropic API 過負荷 (3回リトライ失敗)")
        # トークン使用量をログに記録
        try:
            log_file = os.path.join(_BASE, "api_usage_log.json")
            entries = []
            if os.path.exists(log_file):
                with open(log_file, encoding="utf-8") as f:
                    entries = json.load(f)
            entries.append({
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user": USERNAME,
                "in": resp.usage.input_tokens,
                "out": resp.usage.output_tokens,
            })
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False)
        except Exception:
            pass

        comment = _trim_to_complete_sentence(resp.content[0].text.strip())
        if comment.upper() == "SKIP":
            print(f"[comment] Claudeがスキップ判定 → スキップ")
            return None
        if (not comment
                or len(comment) > 65
                or any(p in comment for p in _BOT_REVEAL_PATTERNS)):
            print(f"[comment] 生成コメント不適切 → スキップ: {comment!r}")
            return None
        return comment
    except Exception as e:
        print(f"[comment] コメント生成失敗 → スキップ: {e}")
        return None


def _select_topic_for_post(texts: list) -> str:
    """投稿内容に最適なThreadsトピックキーワードをClaude APIで選択して返す。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    post_body = "\n".join(texts)[:600]

    if USERNAME == "bemolle_diet":
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
        prompt = f"""以下のThreads投稿に最もぴったりなトピック（話題カテゴリ）を1つだけ選んでください。
アカウントのテーマ：{account_hint}

【投稿内容】
{post_body}

【選び方の基準】
・Threads内でそのキーワードで検索したときに関連コンテンツが出るような一般的なカテゴリ名
・日本語1〜3語のキーワード（例：美容、ダイエット、起業、AI、Notion、スキンケア）
・投稿の主題を最もよく表すもの

キーワードだけ出力してください。説明・記号・改行は不要です。"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        )
        topic = resp.content[0].text.strip()
        topic = re.sub(r'[「」『』・\n\r]', '', topic).strip()
        print(f"[topic] AI選択トピック: '{topic}'")
        return topic
    except Exception as e:
        chosen = random.choice(fallback_candidates)
        print(f"[topic] AI選択失敗 → フォールバック '{chosen}': {e}")
        return chosen


def _post_comment_to_url(page, post_url, comment_text):
    """post_url の投稿にコメント（返信）を投稿する。"""
    page.goto(post_url, wait_until="domcontentloaded", timeout=25000)
    # インタラクティブ要素の描画を待つ（ボタンは遅れて表示されることがある）
    try:
        page.wait_for_selector('div[data-pressable-container="true"]', timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    post_id = post_url.rstrip("/").split("/")[-1]
    # 元投稿コンテナの返信ボタンを探す
    reply_btn = None
    container = page.locator(f'div[data-pressable-container="true"]:has(a[href*="/post/{post_id}"])').first
    if container.count() > 0:
        for label in ['[aria-label*="返信"]', '[aria-label*="Reply"]']:
            btn = container.locator(label).first
            if btn.count() > 0:
                reply_btn = btn
                break
    if reply_btn is None or reply_btn.count() == 0:
        for label in ['[aria-label*="返信"]', '[aria-label*="Reply"]']:
            btn = page.locator(label).first
            if btn.count() > 0:
                reply_btn = btn
                break
    # コメント制限アカウントの判定（ボタンが存在しない場合）
    if reply_btn is None or reply_btn.count() == 0:
        raise RuntimeError("コメント制限アカウント（返信ボタンが見つかりません）")
    reply_btn.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    reply_btn.click()
    page.wait_for_timeout(2000)
    if page.locator('div[role="dialog"]').count() == 0:
        raise RuntimeError("返信モーダルが開きませんでした")
    _input_text(page, comment_text)
    page.wait_for_timeout(500)
    _click_submit(page)
    page.wait_for_timeout(2000)


def _do_auto_comments(page, dry_run=False):
    """未コメントアカウントへ自動コメント。プール不足時は自動発掘。
    戻り値: [{account, status, url?}]"""
    # プールファイルがなければ空リストで初期化
    if not os.path.exists(COMMENT_TARGETS_FILE):
        targets = []
    else:
        try:
            with open(COMMENT_TARGETS_FILE, encoding="utf-8") as f:
                targets = json.load(f)
        except Exception:
            targets = []

    commented = _load_commented()

    # 未コメントのアカウントを絞り込む
    # verified フィールドがないアカウントを実在確認してプールをクリーンアップ
    unverified = [t for t in targets if t.get("verified") is None]
    if unverified:
        print(f"[verify] 未確認アカウント {len(unverified)} 件を実在確認中...")
        cleaned = []
        pool_changed = False
        for t in targets:
            if t.get("verified") is None:
                account = t.get("account", "")
                if account and _check_account_exists(page, account):
                    t["verified"] = True
                    cleaned.append(t)
                    print(f"[verify] @{account} ✅ 実在確認")
                else:
                    print(f"[verify] @{account} ❌ 存在しない → プールから削除")
                pool_changed = True
            else:
                cleaned.append(t)
        if pool_changed:
            targets = cleaned
            try:
                with open(COMMENT_TARGETS_FILE, "w", encoding="utf-8") as f:
                    json.dump(targets, f, ensure_ascii=False, indent=2)
                print(f"[verify] プール更新完了（{len(targets)} 件）")
            except Exception as e:
                print(f"[verify] プール保存失敗: {e}")

    eligible = [t for t in targets if not _already_commented_recently(commented, t.get("account", ""))]
    pool_accounts = {t.get("account", "") for t in targets}

    # 未コメントが COMMENT_MIN_POOL を下回ったら自動発掘
    if len(eligible) < COMMENT_MIN_POOL:
        # 発掘除外対象：プール内アカウント + 30日以内にコメント済みのアカウント
        # （30日超の既コメント済みは再発掘対象に戻す）
        recently_blocked = {
            re.search(r'/([^/]+)/', k).group(1)
            for k in commented.keys()
            if re.search(r'/([^/]+)/', k)
            and _already_commented_recently(commented, re.search(r'/([^/]+)/', k).group(1))
        }
        already_known = pool_accounts | recently_blocked
        new_targets = _discover_new_accounts(page, already_known, max_new=COMMENT_DISCOVER_MAX)
        if new_targets:
            targets = targets + new_targets
            try:
                with open(COMMENT_TARGETS_FILE, "w", encoding="utf-8") as f:
                    json.dump(targets, f, ensure_ascii=False, indent=2)
                print(f"[discover] {len(new_targets)} 件をプールに追加（合計 {len(targets)} 件）")
            except Exception as e:
                print(f"[discover] プール保存失敗: {e}")
            eligible = [t for t in targets if not _already_commented_recently(commented, t.get("account", ""))]

    # 今回コメントするアカウントをランダム選択
    n = MAX_COMMENTS_PER_RUN if MAX_COMMENTS_PER_RUN > 0 else len(eligible)
    if len(eligible) > n:
        targets = random.sample(eligible, n)
        print(f"[comment] 未コメント{len(eligible)}件中{n}件をランダム選択")
    else:
        targets = eligible
        if not targets:
            print("[comment] コメント可能なアカウントなし")
            return []

    results = []

    for t in targets:
        account = t.get("account", "")
        if not account:
            continue
        try:
            post_url, post_text = _get_target_latest_post(page, account)
            if not post_url:
                print(f"[comment] @{account} 最新投稿取得できず → スキップ")
                results.append({"account": account, "status": "no_post"})
                continue

            log_key = f"/{account}/{post_url.rstrip('/').split('/')[-1]}"
            # コメント直前に再度ファイルを読み直して二重チェック（並走・遅延対策）
            commented = _load_commented()
            _cutoff = datetime.now() - timedelta(days=COMMENT_COOLDOWN_DAYS)
            _same_post_recent = (
                log_key in commented
                and datetime.fromisoformat(commented[log_key]) > _cutoff
            )
            if _same_post_recent or _already_commented_recently(commented, account):
                print(f"[comment] @{account} コメント済み（直前再チェック） → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue

            # 投稿本文が短すぎる（15文字未満）はコメントしない
            if len(post_text.strip()) < 15:
                print(f"[comment] @{account} 投稿が短すぎる（{len(post_text.strip())}文字） → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue

            # 質問型投稿はスキップ
            if any(w in post_text for w in _QUESTION_SKIP_WORDS):
                print(f"[comment] @{account} 質問型投稿 → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue

            # エンタメ・無関係トピックはスキップ
            if any(w in post_text for w in _ENTERTAINMENT_SKIP_WORDS):
                print(f"[comment] @{account} エンタメ系 → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue

            # ブランドイメージ保護・不適切トピックはスキップ
            if any(w in post_text for w in _NEGATIVE_IMPRESSION_SKIP_WORDS):
                print(f"[comment] @{account} マイナス印象トピック → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue

            if any(w in post_text for w in _ILLNESS_DEATH_WORDS):
                print(f"[comment] @{account} 病気・メンタル関連 → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue

            # 投稿内容に合ったコメントをClaude APIで生成
            account_note = t.get("note", "")
            comment_text = _generate_comment(post_text, account_note)
            if comment_text is None:
                print(f"[comment] @{account} 適切なコメント生成不可 → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue
            print(f"[comment] @{account} 生成コメント: 「{comment_text}」")

            if dry_run:
                print(f"[comment][dry_run] @{account}: {post_url}")
                results.append({"account": account, "status": "dry_run", "url": post_url, "comment": comment_text})
                continue

            _post_comment_to_url(page, post_url, comment_text)
            commented[log_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_commented(commented)
            print(f"[comment] @{account} コメント完了 ✅")
            results.append({"account": account, "status": "ok", "url": post_url, "comment": comment_text})

            # アカウント間に少し待機（bot臭を消す）
            page.wait_for_timeout(random.randint(4000, 8000))

        except Exception as e:
            err_msg = str(e)
            print(f"[comment] @{account} コメント失敗: {err_msg}")
            results.append({"account": account, "status": "error", "error": err_msg})
            # コメント制限アカウントはプールから永続除外
            if "コメント制限アカウント" in err_msg:
                try:
                    with open(COMMENT_TARGETS_FILE, encoding="utf-8") as f:
                        pool = json.load(f)
                    pool = [p for p in pool if p.get("account") != account]
                    with open(COMMENT_TARGETS_FILE, "w", encoding="utf-8") as f:
                        json.dump(pool, f, ensure_ascii=False, indent=2)
                    print(f"[comment] @{account} コメント制限 → プールから削除")
                except Exception as fe:
                    print(f"[comment] プール更新失敗: {fe}")

    return results


def _open_composer(page):
    """ホームから新規投稿モーダルを開く。
    プロフィールページに先に遷移してセッションを確立してからホームへ戻る。
    CI環境(Ubuntu)でいきなりホームを開くとセッションが半壊状態になる問題の回避策。"""
    # 既にプロフィールページにいない場合のみ遷移（_verify_accountのUI確認後の二重遷移を避ける）
    if f"/@{USERNAME}" not in page.url:
        page.goto(f"https://www.threads.com/@{USERNAME}", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        _detect_login_required(page)
    page.goto("https://www.threads.com", wait_until="domcontentloaded")
    page.wait_for_timeout(7000)
    _detect_login_required(page)   # cookie切れならここで CookieExpiredError
    opener_selectors = [
        'div[role="button"]:has-text("What\'s new?")',
        'div[role="button"]:has-text("新しい投稿を作成")',
        'div[role="button"]:has-text("いまどうしてる")',
        '[aria-label*="Create"]',
        '[aria-label*="新しい"]',
        'a[href="/intent/post"]',
    ]
    # CI headless環境ではis_visible()がFalseになる場合があるためforce clickで対応
    for sel in opener_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                loc.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                loc.click(force=True)
                page.wait_for_timeout(2500)
                return
            except Exception:
                continue
    # フォールバック: /intent/post に直接遷移してモーダルを開く
    print("[composer] ホームからの起動に失敗。/intent/post へ直接遷移します")
    page.goto("https://www.threads.com/intent/post", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    _detect_login_required(page)
    # テキスト入力エリアが表示されていれば成功
    textbox = page.locator('[contenteditable="true"]').first
    if textbox.count() > 0:
        print("[composer] /intent/post から起動成功")
        return
    # デバッグ用スクリーンショット（失敗時）
    try:
        page.screenshot(path="/tmp/composer_fail.png", full_page=True)
        print(f"[debug] screenshot saved: /tmp/composer_fail.png  URL={page.url}")
        print(f"[debug] body divs={page.locator('div').count()} buttons={page.locator('[role=button]').count()}")
        print(f"[debug] page html (500chars): {page.content()[:500]}")
    except Exception as _e:
        print(f"[debug] screenshot failed: {_e}")
    raise RuntimeError("投稿コンポーザーを開けませんでした")


def _get_latest_post_url(page, after_reply=False, previous_url=None):
    """1部目投稿後（after_reply=False）はプロフィール最上位から取得。
    previous_url が指定されている場合はURLが変わるまでリトライし、
    プロフィールキャッシュによる旧URL誤取得を防ぐ。
    返信投稿後（after_reply=True）は今いるチェーン詳細ページから自分の最新投稿URLを拾う。"""
    if after_reply:
        page.wait_for_timeout(3500)
        locator = page.locator(f'a[href*="/@{USERNAME}/post/"]')
        count = locator.count()
        if count == 0:
            raise RuntimeError("チェーン詳細に自分の投稿リンクが見つかりません")
        seen = []
        for i in range(count):
            href = locator.nth(i).get_attribute("href")
            if href and "/post/" in href and href not in seen:
                seen.append(href)
        if not seen:
            raise RuntimeError("チェーン詳細から有効なpost URLを取得できません")
        return seen[-1]

    # /intent/post 経由で投稿した場合、投稿後にそのポストページへリダイレクトされることがある
    if f"/@{USERNAME}/post/" in page.url:
        direct_url = page.url.split("?")[0].rstrip("/")
        print(f"[debug] 投稿後ページから直接URL取得: {direct_url}")
        return direct_url

    # プロフィールページから取得。previous_url が変わるまで最大5回リトライ
    wait_seq = [5000, 8000, 12000, 16000, 20000]
    for attempt, wait_ms in enumerate(wait_seq):
        page.goto(f"https://www.threads.com/@{USERNAME}", wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        articles = page.locator('div[data-pressable-container="true"]')
        if articles.count() == 0:
            articles = page.locator('article')
        if articles.count() == 0:
            print(f"[debug] プロフィールに記事なし（attempt {attempt + 1}/{len(wait_seq)}）")
            continue
        first = articles.first
        first.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        time_link = first.locator('time').first
        if time_link.count() == 0:
            print(f"[debug] timeリンクなし（attempt {attempt + 1}/{len(wait_seq)}）")
            continue
        href = time_link.evaluate("el => el.closest('a')?.href")
        if not href:
            continue
        if previous_url and href == previous_url:
            print(f"[debug] プロフィール未更新（attempt {attempt + 1}/{len(wait_seq)}）: {href}")
            continue
        print(f"[debug] 最新投稿URL取得（attempt {attempt + 1}）: {href}")
        return href

    raise RuntimeError(f"プロフィールに新投稿が{len(wait_seq)}回試行しても反映されませんでした")


def _check_latest_post_is_recent(page, max_age_min=20):
    """プロフィールページで最新投稿が max_age_min 分以内か確認。None=確認不能。
    60分超の場合はプロフィールキャッシュ未更新の可能性が高いため None を返す。"""
    try:
        from datetime import timezone
        articles = page.locator('div[data-pressable-container="true"]')
        if articles.count() == 0:
            articles = page.locator('article')
        if articles.count() == 0:
            return None
        time_el = articles.first.locator('time[datetime]').first
        if time_el.count() == 0:
            return None
        dt_str = time_el.get_attribute('datetime')
        if not dt_str:
            return None
        post_dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        age_min = (datetime.now(timezone.utc) - post_dt).total_seconds() / 60
        print(f"[verify] 最新投稿 {age_min:.1f}分前")
        if age_min > 60:
            # 60分超 = プロフィールが未更新の可能性大。確認不能として扱う
            print("[verify] 60分超のため確認不能（プロフィール未更新の可能性）→ None")
            return None
        return age_min <= max_age_min
    except Exception as e:
        print(f"[verify] タイムスタンプ確認失敗（続行）: {e}")
        return None


def _open_reply_modal(page, post_url):
    """投稿詳細ページに遷移して、post_url のポスト本体に紐づく「返信」アイコンを押す。
    Threadsの仕様:
      - 1部目のリプライアイコンを押すと「1部目への返信」になり、既存の2部目と兄弟化する
      - 2部目（=チェーン末尾）のリプライアイコンを押すと「2部目への返信」=チェーン延長になる
    なので post_url にマッチする投稿コンテナを特定してから、その配下の「返信」を押す。"""
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    post_id = post_url.rstrip("/").split("/")[-1]
    target = page.locator(
        f'div[data-pressable-container="true"]:has(a[href*="/post/{post_id}"])'
    ).first
    reply_btn = None
    if target.count() > 0:
        reply_btn = target.locator('[aria-label*="返信"]').first
    if reply_btn is None or reply_btn.count() == 0:
        # フォールバック: 末尾のチェーンポスト（自分のpost link を持つ最後のコンテナ）
        own_containers = page.locator(
            f'div[data-pressable-container="true"]:has(a[href*="/@{USERNAME}/post/"])'
        )
        if own_containers.count() > 0:
            reply_btn = own_containers.last.locator('[aria-label*="返信"]').first
    if reply_btn is None or reply_btn.count() == 0:
        raise RuntimeError("返信ボタンが見つかりません")
    reply_btn.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    reply_btn.click()
    page.wait_for_timeout(2500)
    if page.locator('div[role="dialog"]').count() == 0:
        raise RuntimeError("返信モーダルが開きませんでした")


def post_to_threads(texts, debug=False, dry_run=False, topic=""):
    """texts: list[str] または str。
    複数要素なら 1部目を単発投稿 → 自分の投稿に self-reply で 2部目, 3部目...と続けてツリー化。"""
    if isinstance(texts, str):
        texts = [texts]

    with sync_playwright() as p:
        force_headed = os.environ.get("HEADED") == "1"
        is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
        # CI（クラウド）では XServer が無いので常にheadless。
        # ローカルは dry-run時のみheadedで目視確認できる従来挙動を維持。
        headless = True if is_ci else ((not dry_run) and not force_headed)
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            storage_state=SESSION_FILE,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()

        try:
            print(f"[account] 投稿前アカウント確認: USERNAME={USERNAME}, SESSION_FILE={SESSION_FILE}")
            _verify_account(page)  # 先にアカウント確認（edit_profileに移動するのでcomposer前に実行）

            # ツリー投稿用：投稿前の最新URLを記録（後でプロフィール更新を確認するため）
            pre_post_url = None
            if len(texts) > 1:
                try:
                    if f"/@{USERNAME}" not in page.url:
                        page.goto(f"https://www.threads.com/@{USERNAME}", wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)
                    arts = page.locator('div[data-pressable-container="true"]')
                    if arts.count() == 0:
                        arts = page.locator('article')
                    if arts.count() > 0:
                        tl = arts.first.locator('time').first
                        if tl.count() > 0:
                            pre_post_url = tl.evaluate("el => el.closest('a')?.href")
                            print(f"[tree] 投稿前最新URL（基準）: {pre_post_url}")
                except Exception as _e:
                    print(f"[tree] 投稿前URL取得失敗（続行）: {_e}")

            _open_composer(page)   # アカウント確認後にホームへ戻ってモーダルを開く
            _set_topic(page, topic)
            _input_text(page, texts[0])
            if dry_run:
                print("[dry_run] 1部目の入力まで完了。Postは押さずに終了。")
                page.screenshot(path="debug_dry_part1.png", full_page=False)
                browser.close()
                return
            _click_submit(page)

            post_verified = True  # ツリー投稿は _get_latest_post_url が検証を兼ねる
            if len(texts) > 1:
                current_url = _get_latest_post_url(page, previous_url=pre_post_url)
                print(f"[debug] 1部目URL: {current_url}")

                for i, text in enumerate(texts[1:], start=2):
                    print(f"[{i}/{len(texts)}] {i}部目を返信として投稿")
                    _open_reply_modal(page, current_url)
                    _input_text(page, text)
                    _click_submit(page)
                    page.wait_for_timeout(3000)
                    current_url = _get_latest_post_url(page, after_reply=True)
                    print(f"[debug] {i}部目URL: {current_url}")
            else:
                # 単発投稿: プロフィールへ移動してタイムスタンプ確認
                # 15秒待機：/intent/post 経由の場合、プロフィールに反映されるまで時間がかかるため
                page.wait_for_timeout(15000)
                try:
                    _get_latest_post_url(page)  # プロフィールページへ移動
                    post_verified = _check_latest_post_is_recent(page)
                except Exception as e:
                    print(f"[verify] 投稿確認スキップ: {e}")
                    post_verified = None

            # 自動コメント（AUTO_COMMENT=1 の場合のみ）
            comment_results = []
            if AUTO_COMMENT:
                print("[comment] 自動コメント開始...")
                comment_results = _do_auto_comments(page, dry_run=dry_run)

            # 投稿成功後：Playwright が取得した最新 Cookie をファイルに保存
            # GitHub Actions がこれを読んで Secret を上書きすることで Mac 不要の自動更新が実現する
            try:
                context.storage_state(path="session_refreshed.json")
                print(f"[session] 最新 Cookie を session_refreshed.json に保存しました")
            except Exception as e:
                print(f"[session] Cookie 保存スキップ: {e}")

            return {"post_verified": post_verified, "comment_results": comment_results}
        finally:
            browser.close()
    return {"post_verified": None, "comment_results": []}


def _send_line_failure_notify(reason: str, slot: str):
    """投稿未確認・コメント失敗時のLINE通知。"""
    if not LINE_TOKEN:
        return
    slot_label = {"morning": "朝 7:00", "morning2": "朝 9:00", "noon": "昼 12:00", "evening2": "夜 22:00", "evening": "夜 21:00"}.get(slot, slot)
    if reason == "post":
        msg = f"⚠️ 投稿未確認（{slot_label}）\n@{USERNAME}\n\n投稿ボタンは押しましたが、Threadsに反映されていません。手動確認をお願いします。"
    elif reason == "no_comment_targets":
        msg = f"⚠️ コメント対象なし（{slot_label}）\n@{USERNAME}\n\nコメントできるアカウントがプールにありません。キーワード検索でも新規発掘できませんでした。"
    else:
        msg = f"⚠️ 自動コメント全件失敗（{slot_label}）\n@{USERNAME}\n\n投稿は完了しましたが、他アカウントへのコメントに全件失敗しました。"
    try:
        body = {"messages": [{"type": "text", "text": msg}]}
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            print(f"[line] 失敗通知送信完了 status={r.status}")
    except Exception as e:
        print(f"[line] 失敗通知送信失敗（続行）: {e}")


def _send_line_notify(slot: str, texts: list, comment_results: list = None):
    """投稿完了後にLINEへ通知。LINE_CHANNEL_ACCESS_TOKEN が未設定なら何もしない。"""
    if not LINE_TOKEN:
        return
    slot_label = {"morning": "朝 7:00", "morning2": "朝 9:00", "noon": "昼 12:00", "evening2": "夜 22:00", "evening": "夜 21:00"}.get(slot, slot)
    content = "\n\n↩️ ツリー返信\n".join(texts) if len(texts) > 1 else texts[0]

    targets_msg = ""
    if comment_results:
        # 自動コメント済みの場合：結果を表示
        status_icon = {"ok": "✅", "skipped": "⏭️", "error": "❌", "no_post": "⚠️", "dry_run": "🧪"}
        lines = []
        for r in comment_results:
            icon = status_icon.get(r.get("status", ""), "❓")
            account = r.get("account", "")
            comment = r.get("comment", "")
            line = f"{icon} @{account}"
            if comment and r.get("status") in ("ok", "dry_run"):
                line += f"\n   「{comment}」"
            lines.append(line)
        targets_msg = "\n\n──────────\n🤖 自動コメント完了\n\n" + "\n\n".join(lines)
    elif os.path.exists(COMMENT_TARGETS_FILE):
        # 手動コメント案を表示（AUTO_COMMENT=0 の場合）
        try:
            with open(COMMENT_TARGETS_FILE, encoding="utf-8") as f:
                targets = json.load(f)
            lines = []
            for i, t in enumerate(targets, 1):
                lines.append(f"{i}. @{t['account']}\n   ↳ {t['comment']}")
            if lines:
                targets_msg = "\n\n──────────\n💬 コメントしに行く（5分でOK）\n\n" + "\n\n".join(lines)
        except Exception:
            pass

    msg = f"📱 Threads投稿完了（{slot_label}）\n@{USERNAME}\n\n{content}{targets_msg}"
    try:
        body = {"messages": [{"type": "text", "text": msg}]}
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            print(f"[line] 通知送信完了 status={r.status}")
    except Exception as e:
        print(f"[line] 通知送信失敗（続行）: {e}")


def _git_pull_latest():
    """最新のlast_run.jsonを取得するためgit pullを実行。失敗しても続行。"""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            capture_output=True, timeout=30, cwd=_BASE
        )
        if result.returncode == 0:
            print("[guard] git pull完了（最新state確認済み）")
        else:
            print(f"[guard] git pull失敗（続行）: {result.stderr.decode()[:100]}")
    except Exception as e:
        print(f"[guard] git pull例外（続行）: {e}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ["morning", "morning2", "noon", "evening2", "evening"]:
        print("使い方: python3 post.py morning|morning2|noon|evening2|evening [--dry-run]")
        sys.exit(1)

    time_slot = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    # 投稿前に最新のlast_run.jsonを取得（cron遅延発火による重複投稿防止）
    if not dry_run:
        _git_pull_latest()

    if not dry_run and already_posted_today(time_slot):
        print(f"[skip] {time_slot} は本日すでに投稿済み。終了。")
        return

    if not dry_run and not is_valid_time_for_slot(time_slot):
        h_start, h_end = SLOT_VALID_HOURS.get(time_slot, (0, 24))
        now_hms = datetime.now().strftime('%H:%M')
        print(f"[skip] 現在 {now_hms} JST は {time_slot} の正規時間帯（{h_start:02d}:00〜{h_end:02d}:00）外のため投稿しません。"
              f"手動補完時は SKIP_TIME_GUARD=1 で無効化可。")
        return

    if dry_run:
        texts = ["テスト1部目（dry-run）。", "テスト2部目。", "テスト3部目。"]
        idx = None
    else:
        idx, text = select_post(time_slot)
        texts = [text] if isinstance(text, str) else list(text)
        texts = _maybe_add_instagram_cta(texts)

    # 投稿内容に合ったトピックをAIで選択（dry_run時はフォールバック使用）
    topic = _select_topic_for_post(texts)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = " [DRY-RUN]" if dry_run else ""
    kind = "ツリー" if len(texts) > 1 else "単発"
    print(f"[{now}]{mode} {time_slot} の投稿を開始（{len(texts)}部の{kind}）")
    for i, t in enumerate(texts):
        print(f"--- part {i+1}/{len(texts)} ---\n{t}\n")

    if not dry_run:
        wait_for_network(timeout=240, interval=5)

        # 投稿時刻ジッター（0〜5分のランダム遅延）。毎日同じ時刻ピッタリのbot臭を消す。
        if not os.environ.get("SKIP_JITTER"):
            jitter_sec = random.randint(0, 300)
            print(f"[jitter] 投稿前 {jitter_sec}秒（{jitter_sec//60}分{jitter_sec%60}秒）待機")
            time.sleep(jitter_sec)

    # 投稿失敗時の自動リトライ（最大3回・90秒間隔）。ネタは成功時のみ消費する。
    max_attempts = 1 if dry_run else 3
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = post_to_threads(texts, debug=dry_run, dry_run=dry_run, topic=topic)
            post_verified = result.get("post_verified") if isinstance(result, dict) else None
            comment_results = result.get("comment_results", []) if isinstance(result, dict) else []
            last_exc = None
            break
        except CookieExpiredError as e:
            # cookie切れはリトライしても無駄なので即exit。専用exit codeで workflow に伝える
            print(f"[fatal] Threadsログイン切れ: {e}")
            sys.exit(EXIT_COOKIE_EXPIRED)
        except Exception as e:
            last_exc = e
            print(f"[retry] 試行{attempt}/{max_attempts} 失敗: {e}")
            if attempt < max_attempts:
                # 再試行前にネット疎通だけ再確認
                wait_for_network(timeout=120, interval=5)
                time.sleep(60)
    if last_exc is not None:
        print(f"[fatal] {max_attempts}回試行して投稿できませんでした: {last_exc}")
        sys.exit(EXIT_GENERIC_FAIL)

    print("投稿完了！" if not dry_run else "[dry-run] 終了")

    if not dry_run:
        # 投稿がThreadsに反映されているか確認
        if post_verified is False:
            print("[fatal] 投稿がThreadsに反映されていません")
            _send_line_failure_notify("post", time_slot)
            sys.exit(EXIT_GENERIC_FAIL)

        commit_used(time_slot, idx)
        record_success(time_slot)
        schedule_next_wake(time_slot)

        # コメント結果はログのみ（対応不要のため通知しない）
        if AUTO_COMMENT:
            if not comment_results:
                print("[warn] コメント対象アカウントなし（プール枯渇 or 発掘失敗）")
            else:
                ok_count = sum(1 for r in comment_results if r.get("status") == "ok")
                if ok_count == 0:
                    print("[warn] 自動コメントが全件失敗しました")


if __name__ == "__main__":
    main()
