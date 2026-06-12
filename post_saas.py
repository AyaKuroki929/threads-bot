"""
SaaS版 Threads自動投稿スクリプト
Supabaseから全アクティブサロンのトークンを読み込み、
公式 Threads API で投稿する。
使い方: python3 post_saas.py morning / python3 post_saas.py evening
"""
import sys
import json
import os
import re
import time
import random
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
POSTS_DIR = os.path.join(os.path.dirname(__file__), "posts_saas")

SLOT = sys.argv[1] if len(sys.argv) > 1 else "morning"  # morning / noon / evening
# 特定サロンのみ投稿（テスト/デモ用）。argv[2] または環境変数 ONLY_SALON。
# 通常運用（スケジュール）では空＝全サロン。指定時は重複チェックも飛ばして必ず投稿する。
SALON_FILTER = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ONLY_SALON", "")).strip()
THREADS_API = "https://graph.threads.net/v1.0"


def supabase_get(path, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def supabase_post(path, data):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def get_active_salons():
    return supabase_get("salons", {"is_active": "eq.true", "select": "id,salon_name,threads_user_id,access_token,instagram_url"})


LINE_TOKEN = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")


def _notify_line(message: str):
    if not LINE_TOKEN:
        return
    try:
        body = json.dumps({"messages": [{"type": "text", "text": message}]}).encode()
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=body,
            headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[LINE通知失敗] {e}")


def get_used_posts(salon_id, slot):
    rows = supabase_get("post_logs", {
        "salon_id": f"eq.{salon_id}",
        "slot": f"eq.{slot}",
        "select": "post_content",
    })
    return {r["post_content"] for r in rows}


def already_posted_today(salon_id, slot):
    # 「今日」はJST(Asia/Tokyo)基準で判定する。
    # 投稿はJSTスケジュール(7/12/21時)だが朝7時=UTC前日22時で、UTC日付だと
    # UTCの境目(0時UTC=朝9時JST)を朝投稿がまたぎ、前日分を当日扱いして誤スキップ→
    # 遅延した予備実行が二重投稿し、朝投稿が9時台に固定される不具合があった。
    start_jst = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_jst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = supabase_get("post_logs", {
        "salon_id": f"eq.{salon_id}",
        "slot": f"eq.{slot}",
        "posted_at": f"gte.{start_utc}",
        "select": "id",
        "limit": "1",
    })
    return len(rows) > 0


def _safe_name(salon_name):
    return re.sub(r'[^\w\-]', '_', salon_name)


def pick_post(salon_name, slot, used_texts):
    posts_file = os.path.join(POSTS_DIR, f"posts_{_safe_name(salon_name)}.json")
    if not os.path.exists(posts_file):
        raise FileNotFoundError(f"投稿ファイルが見つかりません: {posts_file}")

    with open(posts_file) as f:
        data = json.load(f)

    candidates = data.get(slot, [])
    if not candidates:
        # フォールバック：このスロットが空でも、他スロットの投稿を借りて投稿を止めない
        for alt in ("morning", "noon", "evening"):
            if data.get(alt):
                candidates = data[alt]
                print(f"[fallback] {salon_name}: {slot}が空 → {alt}スロットから投稿")
                break
    if not candidates:
        raise ValueError(f"{salon_name}: 全スロットの投稿がありません")

    def _key(p):
        return p if isinstance(p, str) else p[0]

    unused = [p for p in candidates if _key(p) not in used_texts]
    if not unused:
        unused = candidates

    chosen = random.choice(unused)
    return chosen if isinstance(chosen, list) else [chosen]


_INSTAGRAM_CTA_TEMPLATES = [
    "\ninstagram.com/{handle} に施術写真を載せています。",
    "\ninstagram.com/{handle} にBeforeAfterを載せています。",
    "\ninstagram.com/{handle} にお客様の声を載せています。",
    "\ninstagram.com/{handle} に施術の様子を載せています。",
    "\ninstagram.com/{handle} にサロンの写真を載せています。",
]


def _maybe_add_instagram_cta_saas(texts: list, instagram_url: str) -> list:
    """instagram_urlが設定されているサロンのみ、1/4の確率でCTAを末尾に追加。"""
    if not instagram_url or random.random() >= 0.25:
        return texts
    handle = instagram_url.rstrip("/").split("/")[-1].lstrip("@")
    if not handle:
        return texts
    cta = random.choice(_INSTAGRAM_CTA_TEMPLATES).format(handle=handle)
    result = list(texts)
    result[-1] = result[-1].rstrip() + cta
    print(f"[cta] Instagram誘導追加: 「{cta.strip()}」")
    return result


class TokenExpiredError(Exception):
    pass


def _keyword_topic(text):
    """投稿内容のキーワードからトピックを判定する（AIフォールバック用）。"""
    t = text
    if any(k in t for k in ["ダイエット", "痩せ", "体重", "脂肪", "減量", "体型"]):
        return "ダイエット"
    if any(k in t for k in ["肌", "スキン", "美肌", "毛穴", "シミ", "乾燥", "保湿", "ニキビ"]):
        return "スキンケア"
    if any(k in t for k in ["エステ", "フェイシャル", "施術", "トリートメント", "脱毛"]):
        return "エステ"
    if any(k in t for k in ["健康", "腸活", "免疫", "睡眠", "疲れ", "体調"]):
        return "健康"
    return "美容"


def _select_topic(texts, salon_name=""):
    """投稿内容に最適なトピックをClaude APIで選択して返す（SaaS版：全サロン対応）。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    post_body = "\n".join(texts)[:600]
    account_hint = f"美容サロン・エステ・スキンケア・ダイエット（{salon_name}）" if salon_name else "美容サロン・エステ・スキンケア・ダイエット"

    if not api_key:
        chosen = _keyword_topic(post_body)
        print(f"[topic:{salon_name}] APIキー未設定 → キーワード判定: '{chosen}'")
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
・日本語1〜3語のキーワード（例：美容、エステ、ダイエット、スキンケア）
・投稿の主題を最もよく表すもの

キーワードだけ出力してください。説明・記号・改行は不要です。"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        )
        topic = re.sub(r'[「」『』・\n\r]', '', resp.content[0].text).strip()
        print(f"[topic:{salon_name}] AI選択: '{topic}'")
        return topic
    except Exception as e:
        chosen = _keyword_topic(post_body)
        print(f"[topic:{salon_name}] AI選択失敗 → キーワード判定 '{chosen}': {e}")
        return chosen


def get_user_id_from_token(token):
    """トークンから実際のuser_idを取得（/me エンドポイント）"""
    url = f"{THREADS_API}/me?fields=id,username&access_token={token}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read())
    return str(d["id"]), d.get("username", "")


def supabase_patch(path, data, params):
    url = f"{SUPABASE_URL}/rest/v1/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def _api_post(user_id, token, text, reply_to_id=None, topic_tag=None):
    create_url = f"{THREADS_API}/{user_id}/threads"
    payload = {
        "media_type": "TEXT",
        "text": text,
        "access_token": token,
    }
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    if topic_tag:
        payload["topic_tag"] = topic_tag

    def _create_container(pl):
        data = urllib.parse.urlencode(pl).encode()
        req = urllib.request.Request(create_url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())["id"]
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code in (401, 403) or "token" in body.lower():
                raise TokenExpiredError(f"トークン切れ HTTP {e.code}: {body[:150]}")
            raise RuntimeError(f"コンテナ作成失敗 HTTP {e.code}: {body[:150]}")

    try:
        creation_id = _create_container(payload)
    except RuntimeError:
        if "topic_tag" in payload:
            print(f"[topic] topic_tag='{payload['topic_tag']}' が拒否された → トピックなしで再試行")
            payload_no_topic = {k: v for k, v in payload.items() if k != "topic_tag"}
            creation_id = _create_container(payload_no_topic)
        else:
            raise

    time.sleep(3)

    publish_url = f"{THREADS_API}/{user_id}/threads_publish"
    publish_data = urllib.parse.urlencode({
        "creation_id": creation_id,
        "access_token": token,
    }).encode()
    req = urllib.request.Request(publish_url, data=publish_data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"公開失敗 HTTP {e.code}: {body[:150]}")


THREADS_TEXT_LIMIT = 500


def _split_long(text, limit=480):
    """Threadsの500字制限の安全網：limitを超えるテキストを文/段落境界で分割する。
    通常は生成側で350字以内に収まるが、万一の超過でも投稿失敗しないようツリー分割する。"""
    text = str(text)
    if len(text) <= limit:
        return [text]
    parts = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("！"),
                  window.rfind("？"), window.rfind("\n"))
        if cut < int(limit * 0.5):
            cut = limit - 1  # 良い区切りがなければ強制カット
        parts.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


def _enforce_threads_limit(texts):
    """各パートが500字以内になるよう必要なら分割。texts: list[str] → list[str]"""
    out = []
    for t in texts:
        out.extend(_split_long(t))
    return out


# Meta(Threads)側の一時障害（HTTP 5xx / 429 / is_transient）は数分続くことがある。
# その場合は段階的に待ち時間を延ばして粘る（30秒→2分→5分）。待っている間に
# Metaが復旧すれば投稿成功する。通常エラー（400等の恒久エラー）は短い待ちで済ます。
TRANSIENT_RETRY_WAITS = [30, 120, 300]  # 一時障害時の待機秒（失敗回数ごと）
QUICK_RETRY_WAIT = 10                    # その他エラー時の待機秒
MAX_POST_ATTEMPTS = len(TRANSIENT_RETRY_WAITS) + 1  # 合計4回試行


def _is_transient_error(e):
    """Meta側の一時障害（リトライで回復しうる）かどうか。"""
    s = str(e)
    return any(marker in s for marker in (
        "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "HTTP 429", "is_transient"))


def threads_post(user_id, token, texts, topic_tag=None):
    """単発またはツリー投稿。texts: list[str]"""
    reply_to_id = None
    first_post_id = None
    for i, text in enumerate(texts):
        tag = topic_tag
        last_exc = None
        for attempt in range(1, MAX_POST_ATTEMPTS + 1):
            try:
                post_id = _api_post(user_id, token, text,
                                    reply_to_id=reply_to_id,
                                    topic_tag=tag)
                last_exc = None
                break
            except TokenExpiredError:
                raise
            except Exception as e:
                last_exc = e
                print(f"[api] part {i+1}/{len(texts)} 試行{attempt}/{MAX_POST_ATTEMPTS} 失敗: {e}")
                if attempt < MAX_POST_ATTEMPTS:
                    if _is_transient_error(e):
                        wait = TRANSIENT_RETRY_WAITS[attempt - 1]
                        print(f"[api]   → 一時障害の疑い。{wait}秒待って再試行")
                    else:
                        wait = QUICK_RETRY_WAIT
                        print(f"[api]   → {wait}秒待って再試行")
                    time.sleep(wait)
        if last_exc:
            raise last_exc

        if i == 0:
            first_post_id = post_id
            reply_to_id = post_id
        print(f"[api] part {i+1}/{len(texts)} 投稿完了: post_id={post_id}")
        if i < len(texts) - 1:
            time.sleep(3)
    return first_post_id


def log_post(salon_id, slot, text):
    supabase_post("post_logs", {
        "salon_id": salon_id,
        "slot": slot,
        "post_content": text,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    })


def main():
    salons = get_active_salons()
    if not salons:
        print("アクティブなサロンがありません")
        return

    if SALON_FILTER:
        salons = [s for s in salons if s.get("salon_name") == SALON_FILTER]
        print(f"[filter] 対象サロンを {SALON_FILTER} のみに限定（{len(salons)}件）")
        if not salons:
            print(f"[filter] {SALON_FILTER} が見つかりません")
            return

    results = {"ok": [], "error": [], "token_expired": []}

    for salon in salons:
        salon_id = salon["id"]
        salon_name = salon["salon_name"]
        user_id = salon["threads_user_id"]
        token = salon["access_token"]

        account_label = salon_name
        try:
            # /me でusernameを取得（通知に使う）。user_id未設定なら同時に保存
            try:
                fetched_id, uname = get_user_id_from_token(token)
                if not user_id:
                    supabase_patch("salons", {"threads_user_id": fetched_id}, {"id": f"eq.{salon_id}"})
                    print(f"[{salon_name}] user_id={fetched_id} (@{uname}) を Supabase に保存")
                user_id = fetched_id
            except Exception as e:
                uname = ""
                print(f"[{salon_name}] /me 失敗（続行）: {e}")
                if not user_id:
                    raise

            account_label = f"@{uname}" if uname else salon_name

            if not SALON_FILTER and already_posted_today(salon_id, SLOT):
                print(f"[SKIP] {salon_name}: {SLOT} は本日投稿済み（重複実行を防止）")
                results["ok"].append(salon_name)
                continue

            used = get_used_posts(salon_id, SLOT)
            texts = pick_post(salon_name, SLOT, used)
            texts = _maybe_add_instagram_cta_saas(texts, salon.get("instagram_url") or "")
            texts = _enforce_threads_limit(texts)  # 安全網：500字超は自動でツリー分割
            topic_tag = _select_topic(texts, salon_name)

            post_id = threads_post(user_id, token, texts, topic_tag=topic_tag)

            log_post(salon_id, SLOT, texts[0])
            print(f"[OK] {salon_name}: post_id={post_id}")
            results["ok"].append(salon_name)
        except TokenExpiredError as e:
            print(f"[TOKEN_EXPIRED] {salon_name}: {e}")
            results["token_expired"].append(salon_name)
            _notify_line(f"🔑 とうこさん トークン切れ\n\nアカウント：{account_label}\nスロット：{SLOT}\n\nThreadsとの再連携が必要です。")
        except Exception as e:
            print(f"[ERROR] {salon_name}: {e}")
            results["error"].append(f"{salon_name}: {e}")
            _notify_line(f"⚠️ とうこさん 投稿エラー\n\nアカウント：{account_label}\nスロット：{SLOT}\nエラー：{str(e)[:100]}")

    print(f"\n完了: 成功={len(results['ok'])} 失敗={len(results['error'])} トークン切れ={len(results['token_expired'])}")
    if results["token_expired"]:
        sys.exit(3)  # token expiry → workflow側で専用通知
    if results["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
