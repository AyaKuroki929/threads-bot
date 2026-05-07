"""
Threadsに自動投稿するスクリプト（self-reply chain でツリー投稿）
使い方: python3 post.py morning  /  python3 post.py noon  /  python3 post.py evening
"""
import sys
import json
import random
import os
import subprocess
import time
import urllib.request
import urllib.error
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
MAX_COMMENTS_PER_RUN = int(os.environ.get("MAX_COMMENTS_PER_RUN", "0"))
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
    URLにloginが含まれるか、ログインフォーム入力欄が表示されているかで判定。"""
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


def _verify_account(page):
    """ログイン中アカウントが USERNAME と一致するか確認。不一致なら即 SystemExit。
    投稿前に必ず呼ぶ。誤アカウントへの投稿を防ぐ最終防波堤。"""
    try:
        # ホームにアクセスしてナビのプロフィールリンクで確認（最も確実な方法）
        # 自分のプロフィールページにアクセスし「プロフィールを編集」ボタンで確認
        # このボタンは自分のプロフィールにしか表示されないため確実
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
        # 編集ボタンがない = 自分のプロフィールではない
        actual_url = page.url
        print(f"[account] ❌ アカウント不一致。期待: @{USERNAME} / URL: {actual_url}")
        print(f"[account] THREADS_SESSION シークレットが @{USERNAME} のセッションか確認してください。")
        sys.exit(4)
    except SystemExit:
        raise
    except CookieExpiredError:
        raise
    except Exception as e:
        # 確認できなかった場合は警告のみ（投稿は続行）
        print(f"[account] ⚠️ アカウント確認スキップ（確認不能）: {e}")


def already_posted_today(time_slot):
    """今日その時間帯に投稿済みか。cron多重発火時の重複防止。
    各slotには「妥当な投稿時間帯」を設定し、その範囲内の記録のみ「投稿済み」と判定する。
    範囲外（例: evening が深夜0時に記録）は遅延発火扱いで無効 → 正規時間帯に再発火させる。

    JST時間帯：
    - morning: 5:00〜10:59
    - noon:    11:00〜16:59
    - evening: 17:00〜23:59
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
        "morning":  (5, 11),
        "morning2": (8, 12),
        "noon":     (11, 17),
        "evening2": (16, 20),
        "evening":  (17, 24),
    }
    h_start, h_end = valid_hours.get(time_slot, (0, 24))
    return h_start <= last_dt.hour < h_end


# slotごとの「現在時刻が投稿していい時間帯か」判定用（同じテーブル）
SLOT_VALID_HOURS = {
    "morning":  (5, 11),
    "morning2": (8, 12),
    "noon":     (11, 17),
    "evening2": (16, 20),
    "evening":  (17, 24),
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
    textarea.click()
    page.wait_for_timeout(600)
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
    target.click()
    for _ in range(30):
        page.wait_for_timeout(500)
        if page.locator('div[role="dialog"]').count() == 0:
            break


def _set_topic(page, topic: str):
    """コンポーザーモーダル内でトピックを設定する。失敗しても投稿は続行。"""
    if not topic:
        return
    try:
        dialog = page.locator('div[role="dialog"]').last
        topic_btn = None
        for sel in [
            '[aria-label*="トピック"]',
            '[aria-label*="Topic"]',
            '[aria-label*="topic"]',
            'div[role="button"]:has-text("トピックを追加")',
            'div[role="button"]:has-text("Add topic")',
        ]:
            loc = dialog.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                topic_btn = loc
                break
        if topic_btn is None:
            print(f"[topic] ボタンが見つかりません。トピックなしで続行。")
            return
        topic_btn.click()
        page.wait_for_timeout(1500)
        # 検索ボックスにトピック名を入力
        search_box = page.locator('input[placeholder*="トピック"], input[placeholder*="topic"], input[placeholder*="Topic"]').first
        if search_box.count() > 0:
            search_box.fill(topic)
            page.wait_for_timeout(1000)
            # 候補の最初の項目を選択
            candidate = page.locator('div[role="option"], div[role="listitem"]').first
            if candidate.count() > 0:
                candidate.click()
                page.wait_for_timeout(800)
                print(f"[topic] '{topic}' を設定しました。")
            else:
                print(f"[topic] 候補が見つかりません。トピックなしで続行。")
        else:
            print(f"[topic] 検索ボックスが見つかりません。トピックなしで続行。")
    except Exception as e:
        print(f"[topic] 設定失敗（続行）: {e}")


def _load_commented():
    """コメント済みログ読み込み。{key: iso_datetime} の辞書。"""
    if not os.path.exists(COMMENTED_FILE):
        return {}
    try:
        with open(COMMENTED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_commented(data):
    with open(COMMENTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _already_commented_today(commented, account):
    """今日そのアカウントにすでにコメント済みか（URL問わず）"""
    today = datetime.now().strftime("%Y-%m-%d")
    return any(f"/{account}/" in k and v.startswith(today) for k, v in commented.items())


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


_FALLBACK_COMMENTS = [
    "参考になります❤️",
    "この視点、なかったです。ありがとうございます。",
    "腑に落ちました。",
    "これ、まさに感じてたことです。",
    "保存しました。",
]


def _generate_comment(post_text: str, account_note: str) -> str:
    """投稿内容に合ったコメントをClaude APIで生成。APIキー未設定はフォールバック。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not post_text.strip():
        return random.choice(_FALLBACK_COMMENTS)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""あなたは美容サロンオーナー兼AI自動化実践者（@aya_kuroki_0929）です。
以下の投稿に対して、自然で短い共感コメントを1つ生成してください。

【投稿者の特徴】
{account_note}

【投稿内容】
{post_text[:400]}

【ルール】
- 15〜45文字程度
- 敬語ベースだが堅すぎない自然な口調
- 投稿内容に具体的に反応する（「参考になります」だけでなく、何が参考になったかが感じられるもの）
- ハッシュタグなし・絵文字は1個まで（なくてもOK）
- サロンオーナー目線で書く
- 宣伝・自己PRにならない

コメント本文のみを出力してください。"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"[comment] コメント生成失敗 → フォールバック: {e}")
        return random.choice(_FALLBACK_COMMENTS)


def _post_comment_to_url(page, post_url, comment_text):
    """post_url の投稿にコメント（返信）を投稿する。"""
    page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(3000)
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
    if reply_btn is None or reply_btn.count() == 0:
        raise RuntimeError("返信ボタンが見つかりません")
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
    """コメントターゲット全件に自動コメント。今日コメント済みはスキップ。
    戻り値: [{account, status, url?}]"""
    if not os.path.exists(COMMENT_TARGETS_FILE):
        return []
    try:
        with open(COMMENT_TARGETS_FILE, encoding="utf-8") as f:
            targets = json.load(f)
    except Exception:
        return []

    commented = _load_commented()
    results = []

    # MAX_COMMENTS_PER_RUN が設定されている場合、今日未コメントのアカウントからランダムに選ぶ
    if MAX_COMMENTS_PER_RUN > 0:
        eligible = [t for t in targets if not _already_commented_today(commented, t.get("account", ""))]
        if len(eligible) > MAX_COMMENTS_PER_RUN:
            targets = random.sample(eligible, MAX_COMMENTS_PER_RUN)
            print(f"[comment] {len(eligible)}件中{MAX_COMMENTS_PER_RUN}件をランダム選択")
        else:
            targets = eligible

    for t in targets:
        account = t.get("account", "")
        if not account:
            continue
        try:
            if _already_commented_today(commented, account):
                print(f"[comment] @{account} 本日コメント済み → スキップ")
                results.append({"account": account, "status": "skipped"})
                continue

            post_url, post_text = _get_target_latest_post(page, account)
            if not post_url:
                print(f"[comment] @{account} 最新投稿取得できず → スキップ")
                results.append({"account": account, "status": "no_post"})
                continue

            log_key = f"/{account}/{post_url.rstrip('/').split('/')[-1]}"
            if log_key in commented:
                print(f"[comment] @{account} この投稿は既コメント済み → スキップ")
                results.append({"account": account, "status": "skipped", "url": post_url})
                continue

            # 投稿内容に合ったコメントをClaude APIで生成
            account_note = t.get("note", "")
            comment_text = _generate_comment(post_text, account_note)
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
            print(f"[comment] @{account} コメント失敗: {e}")
            results.append({"account": account, "status": "error", "error": str(e)})

    return results


def _open_composer(page):
    """ホームから新規投稿モーダルを開く。"""
    page.goto("https://www.threads.com", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    _detect_login_required(page)   # cookie切れならここで CookieExpiredError
    opener_selectors = [
        'div[role="button"]:has-text("What\'s new?")',
        'div[role="button"]:has-text("新しい投稿を作成")',
        'div[role="button"]:has-text("いまどうしてる")',
        '[aria-label*="Create"]',
        '[aria-label*="新しい"]',
        'a[href="/intent/post"]',
    ]
    for sel in opener_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0 and loc.is_visible():
            loc.click()
            page.wait_for_timeout(2500)
            return
    raise RuntimeError("投稿コンポーザーを開けませんでした")


def _get_latest_post_url(page, after_reply=False):
    """1部目投稿後（after_reply=False）はプロフィール最上位から取得。
    返信投稿後（after_reply=True）は今いるチェーン詳細ページから自分の最新投稿URLを拾う。
    プロフィールから取るとチェーンの頭（=1部目）が返ってくるため、3部目以降が
    1部目への兄弟返信になりツリーが分裂する不具合を防ぐ。"""
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

    page.goto(f"https://www.threads.com/@{USERNAME}", wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    articles = page.locator('div[data-pressable-container="true"]')
    if articles.count() == 0:
        articles = page.locator('article')
    if articles.count() == 0:
        raise RuntimeError("最新投稿が見つかりません")
    first = articles.first
    first.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    time_link = first.locator('time').first
    if time_link.count() == 0:
        raise RuntimeError("最新投稿のtimeリンクが見つかりません")
    href = time_link.evaluate("el => el.closest('a')?.href")
    if not href:
        raise RuntimeError("最新投稿のhrefが取得できません")
    return href


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


def post_to_threads(texts, debug=False, dry_run=False):
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
            _open_composer(page)   # アカウント確認後にホームへ戻ってモーダルを開く
            _set_topic(page, TOPIC)
            _input_text(page, texts[0])
            if dry_run:
                print("[dry_run] 1部目の入力まで完了。Postは押さずに終了。")
                page.screenshot(path="debug_dry_part1.png", full_page=False)
                browser.close()
                return
            _click_submit(page)

            if len(texts) > 1:
                page.wait_for_timeout(3000)
                current_url = _get_latest_post_url(page)
                print(f"[debug] 1部目URL: {current_url}")

                for i, text in enumerate(texts[1:], start=2):
                    print(f"[{i}/{len(texts)}] {i}部目を返信として投稿")
                    _open_reply_modal(page, current_url)
                    _input_text(page, text)
                    _click_submit(page)
                    page.wait_for_timeout(3000)
                    current_url = _get_latest_post_url(page, after_reply=True)
                    print(f"[debug] {i}部目URL: {current_url}")

            # 自動コメント（AUTO_COMMENT=1 の場合のみ）
            comment_results = []
            if AUTO_COMMENT:
                print("[comment] 自動コメント開始...")
                comment_results = _do_auto_comments(page, dry_run=dry_run)
            return comment_results
        finally:
            browser.close()
    return []


def _send_line_notify(slot: str, texts: list, comment_results: list = None):
    """投稿完了後にLINEへ通知。LINE_CHANNEL_ACCESS_TOKEN が未設定なら何もしない。"""
    if not LINE_TOKEN:
        return
    slot_label = {"morning": "朝 7:00", "morning2": "朝 9:00", "noon": "昼 12:00", "evening2": "夕 18:00", "evening": "夜 21:00"}.get(slot, slot)
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

    msg = f"📱 Threads投稿完了（{slot_label}）\n\n{content}{targets_msg}"
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


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ["morning", "morning2", "noon", "evening2", "evening"]:
        print("使い方: python3 post.py morning|morning2|noon|evening2|evening [--dry-run]")
        sys.exit(1)

    time_slot = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

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
            comment_results = post_to_threads(texts, debug=dry_run, dry_run=dry_run)
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
        commit_used(time_slot, idx)
        record_success(time_slot)
        schedule_next_wake(time_slot)
        _send_line_notify(time_slot, texts, comment_results if AUTO_COMMENT else None)


if __name__ == "__main__":
    main()
