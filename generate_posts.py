#!/usr/bin/env python3
"""
投稿ストックが残り THRESHOLD 本以下のスロットに対して
Claude API で新規投稿を GENERATE_COUNT 本自動生成して posts.json に追記する。

使い方:
  python3 generate_posts.py bemolle            # posts.json / used_posts.json
  python3 generate_posts.py personal           # posts_personal.json / used_posts_personal.json
  python3 generate_posts.py saas_bemolle       # posts_saas/posts_bemolle_diet.json / Supabase
  python3 generate_posts.py saas_personal      # posts_saas/posts_aya_0929_private.json / Supabase
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse

THRESHOLD = 5
GENERATE_COUNT = 12
GEN_MODEL = "claude-sonnet-4-6"
GEN_MAX_TOKENS = 8000          # 12本×2部のJSONが4000で途切れた実害（2026-09-21）
GEN_ATTEMPTS = 3               # 生成→読み取り→検証を通るまでの試行回数（初回込み）
CALL_TIMEOUT_SEC = 60          # API 1回の通信期限
# この実行全体の期限。ジョブ(15分)の中で投稿(最大8分)・通知・保存の時間を残すため、
# 1アカウントあたり既定120秒。超えたら残りの枠は諦めて異常終了（沈黙にはしない）
REFILL_DEADLINE_SEC = int(os.environ.get("REFILL_DEADLINE_SEC", "90"))
_DEADLINE = time.monotonic() + REFILL_DEADLINE_SEC


def _time_left() -> float:
    return _DEADLINE - time.monotonic()


class _RefillTimeout(Exception):
    pass


def _arm_hard_deadline() -> None:
    """期限を実時間で強制する。HTTPのtimeoutは『待ち時間』の上限で経過時間の上限ではないため、
    少しずつ受信が続くと120秒を超えられた（2026-09-21 Sol指摘）。alarmで通信中でも中断する。"""
    import signal

    def _on_alarm(signum, frame):
        raise _RefillTimeout(f"実行全体の期限 {REFILL_DEADLINE_SEC}秒を超過")

    try:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(REFILL_DEADLINE_SEC)
    except (AttributeError, ValueError):
        pass   # SIGALRM が無い環境（Windows等）では従来の残時間チェックのみ


def _disarm_deadline() -> None:
    """保存（ファイル書き込み）中に期限で中断されて壊れたJSONを残さないよう、生成が終わったら解除する。"""
    try:
        import signal
        signal.alarm(0)
    except (AttributeError, ValueError):
        pass


def _save_json_atomic(path: str, data) -> None:
    """書きかけで止まっても元ファイルが壊れないよう、一時ファイルに書いてから差し替える。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _extract_json_array(raw: str) -> list:
    """本文から最初の「配列として読めるJSON」を取り出す。
    前後に説明文や角括弧が混ざっても、先頭の [ から順に raw_decode で試すので壊れない
    （最初の[〜最後の]を切る方式は、末尾に『補足 ]』が付くだけで崩れた・2026-09-21 Sol指摘）。"""
    import re
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    dec = json.JSONDecoder()

    def _looks_like_posts(arr) -> bool:
        # 投稿らしさ＝要素が「文字列」か「文字列だけの配列」で、1つ以上ある
        return bool(arr) and all(
            (isinstance(x, str) and x.strip())
            or (isinstance(x, list) and x and all(isinstance(y, str) for y in x))
            for x in arr)

    candidates = []
    idx = s.find("[")
    while idx != -1:
        try:
            obj, used = dec.raw_decode(s[idx:])
            if isinstance(obj, list):
                candidates.append(obj)
                idx = s.find("[", idx + max(used, 1))   # 読めた範囲の内側は飛ばす
                continue
        except json.JSONDecodeError:
            pass
        idx = s.find("[", idx + 1)
    # 説明文中の [] や [1] を本体と取り違えない：投稿らしい配列のうち最大の物を採用（2026-09-21 Sol指摘）
    good = [c for c in candidates if _looks_like_posts(c)]
    if good:
        return max(good, key=len)
    raise ValueError("投稿として読めるJSON配列が見つからない")


def _generate_valid_posts(client, system_prompt, user_prompt, label: str, keep_fn):
    """生成→JSON読み取り→検証を「検証済みの投稿が1本以上残る」まで最大 GEN_ATTEMPTS 回。
    途切れ(max_tokens)・JSON崩れ・全件検証NG・API例外はすべて再試行の対象。
    期限(_DEADLINE)が近いときは打ち切る。戻り値 = (投稿リスト, 失敗理由)。成功なら理由は ""。"""
    reason = ""
    for attempt in range(1, GEN_ATTEMPTS + 1):
        left = _time_left()
        if left < 20:
            return [], f"期限切れ（残り{int(left)}秒・{attempt - 1}回試行）"
        try:
            resp = client.with_options(
                timeout=min(CALL_TIMEOUT_SEC, left), max_retries=0,   # 再試行は外側ループが担う（期限管理を一元化）
            ).messages.create(
                model=GEN_MODEL, max_tokens=GEN_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except _RefillTimeout as e:
            return [], f"期限切れ（通信中に超過）: {e}"
        except Exception as e:
            reason = f"API失敗: {e}"
            print(f"[generate] {label}: {reason} → 再試行 {attempt}/{GEN_ATTEMPTS}")
            continue
        if getattr(resp, "stop_reason", None) == "max_tokens":
            reason = "出力が上限で途切れた"
            print(f"[generate] {label}: {reason} → 再試行 {attempt}/{GEN_ATTEMPTS}")
            continue
        raw = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", "") == "text").strip()
        try:
            arr = _extract_json_array(raw)
        except ValueError as e:
            reason = f"JSON崩れ: {e}"
            print(f"[generate] {label}: {reason} → 再試行 {attempt}/{GEN_ATTEMPTS}（先頭: {raw[:80]!r}）")
            continue
        kept = keep_fn(arr)
        if kept:
            return kept, ""
        reason = f"読み取れたが検証を通る投稿が0本（{len(arr)}本中）"
        print(f"[generate] {label}: {reason} → 再試行 {attempt}/{GEN_ATTEMPTS}")
    return [], reason or "不明"
# ツリー1部目の長さ。ここがタイムラインに出る部分で、短いほど見られる（2026-09-13 実測）
FIRST_PART_MIN = 25
FIRST_PART_MAX = 160


def _valid_tree(p) -> bool:
    """保存してよい形か。ちょうど2部・1部目が短い・各部が長すぎない。"""
    if not isinstance(p, list) or len(p) != 2:
        return False
    if not all(isinstance(x, str) and x.strip() for x in p):
        return False
    return FIRST_PART_MIN <= len(p[0]) <= FIRST_PART_MAX and all(len(x) <= 400 for x in p)
_BASE = os.path.dirname(os.path.abspath(__file__))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _remaining(posts, used, slot):
    total = len(posts.get(slot, []))
    used_indices = used.get(slot, [])
    return total - len(used_indices)


def _load_facts(salon_name: str):
    """事実照合に使うヒアリング相当の情報（saas_facts.json）。無ければ None。

    ⚠️ ベモーレ・個人の補充経路には形と長さの検査しか無く、
    「夜22時まで営業」「初回7,777円」「新宿駅から徒歩3分」のような
    ヒアリングに無い事実がそのまま保存できていた（2026-09-13 Sol指摘）。"""
    path = os.path.join(_BASE, "saas_facts.json")
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8")).get(salon_name)
    except (OSError, ValueError) as e:
        print(f"[generate] saas_facts.json を読めません（事実照合はスキップ）: {e}")
        return None


def _load_rules(rules_file):
    path = os.path.join(_BASE, rules_file)
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    return ""


def generate_for_account(account, posts_file, used_file, rules_file):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # 設定漏れは「補充不能」なので静かに0で終わらない（2026-09-21 Sol指摘）
        print("[generate] ANTHROPIC_API_KEY が未設定 → 補充不能・異常終了", file=sys.stderr)
        sys.exit(1)

    posts_path = os.path.join(_BASE, posts_file)
    used_path = os.path.join(_BASE, used_file)

    try:
        posts = json.load(open(posts_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[generate] {posts_file} を読めません → 補充不能・異常終了: {e}", file=sys.stderr)
        sys.exit(1)
    used = json.load(open(used_path, encoding="utf-8")) if os.path.exists(used_path) else {}
    rules = _load_rules(rules_file)
    facts = _load_facts(account)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    generated_any = False
    needed_slots, filled_slots = [], []   # 枠ごとに「要る」「足せた」を数える

    all_slots = [s for s in ["morning", "morning2", "noon", "evening2", "evening"] if s in posts]
    if not all_slots:
        print(f"[generate] {posts_file} に投稿枠が1つも無い → 補充不能・異常終了", file=sys.stderr)
        sys.exit(1)
    for slot in all_slots:
        remaining = _remaining(posts, used, slot)
        if remaining > THRESHOLD:
            print(f"[generate] {account} {slot}: 残{remaining}本 → 生成不要")
            continue
        needed_slots.append(slot)

        print(f"[generate] {account} {slot}: 残{remaining}本 ≤ {THRESHOLD} → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7:30頃配信）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容が向く。",
            "morning2": "朝2本目投稿（9:00頃配信）。朝イチより少し落ち着いた時間。具体的なTips・保存型・チェックリスト系が向く。",
            "noon": "昼投稿（12:00頃配信）。移動中・休憩中に読む人向け。共感しやすい体験談・サロンあるある・具体的な失敗談が向く。",
            "evening2": "夜1本目投稿（21:00前後配信）。帰宅後・夕食後の時間。今日起きた気づき・お客様エピソード・共感系が向く。",
            "evening": "夜投稿（21:00頃配信）。1日の終わりに読む人向け。内省・本音・静かな気づき・今日学んだことが向く。",
        }[slot]

        existing_samples = "\n".join([
            str(posts[slot][i])[:80] for i in range(min(5, len(posts[slot])))
        ])

        # ⚠️ 朝・昼・夜すべてツリー。本文が短いほど見られるのが実測で分かったため
        # （2026-09-13。〜100字は200字〜の6〜10倍。朝・昼・夜すべてで一致）。
        # 以前は「ツリーは昼だけ」で、それが朝夜の表示回数を落としていた。
        output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は **ちょうど2要素の配列**（ツリー投稿）です。
["1部目の本文", "2部目の本文"]
※1部目末尾を「予告文」で終えること絶対禁止。「〜を書きます」「〜を話します」
  「〜をお伝えします」「〜を紹介します」「正直に書きます」など、内容を宣言する
  メタ発言は全てNG。話を始めてしまい、一番気になるところで止めること。
※**1部目は60〜100字**。ここがタイムラインに出る部分で、短いほど読まれる（実測）。
※2部目は120〜250字。

出力例:
[
  ["1部目フック。改行は\nで表現。", "2部目は答えから。"],
  ["別の1部目。", "別の2部目。"]
]

JSON配列以外の文字は一切出力しないでください。説明文も不要です。"""

        system_cached = f"""あなたはThreads投稿の専門家です。
以下のルールに厳密に従って、{account}アカウントの投稿を生成してください。

=== 投稿生成ルール ===
{rules}"""

        system_dynamic = f"""=== 時間帯の特性（{slot}） ===
{slot_hint}

=== 医療・成分・学術名称の取り扱い（最優先ルール）===
成分名（ビタミン・ミネラル等）・学術名称・数値データは、上記のサロン情報に明記されているもののみ使用すること。
サロン情報に記載のない成分名・効能・数値・学術名称を創作・推測して書くことは絶対禁止。
週次インサイトに「成分名を列挙せよ」「学術名称を強調せよ」等のルールがあっても、サロン情報に根拠がない場合は無視すること。

{output_format}"""

        system_prompt = [
            {"type": "text", "text": system_cached, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": system_dynamic},
        ]

        user_prompt = f"""{GENERATE_COUNT}本の投稿を生成してください。

必ず守ること：
- ギャップ投稿（期待と現実のズレ）を半数以上に入れる（「〇〇なのに△△」「〇〇じゃない、実は△△」）
- W型ストーリー（失敗→成功→失敗→成功）を複数本に使う
- 黒木さんの実体験を素材にする：素材は上記ルール内の「黒木さん自身のストーリー」「ギャップ一覧」に書かれているものだけを使う（このアカウントのテーマに無関係な素材は使わない）。時期・経緯・程度を盛らない・変えない（ルール内の⚠️厳守注記に従う）
- 同じ素材・同じ角度の投稿を繰り返さない（{GENERATE_COUNT}本全部違う切り口にする）
- **保存型投稿を1〜2本必ず含める**：「〇〇の3つの間違い」「チェックリスト」「〇〇する人としない人の違い」など。末尾に「スクショ保存しておくと便利です」を入れる
- **あるある共感型を1〜2本必ず含める**：「40代ダイエットあるある」「こんな経験ありませんか？」など。ターゲットが「私のことだ」と感じる具体的な描写にする
- **返信誘導投稿を1〜2本必ず含める**：末尾に「〇〇はどちらですか？コメントで教えてください」など読者が思わず答えたくなる問いを入れる。二択・共感確認・悩み募集のどれかを使う
- ハッシュタグは絶対に付けない
- 絵文字は使わない
- 各投稿は独立して読めるものにする
- 1部目は必ずスクロールが止まる1行フックから始める

既存投稿のサンプル（この角度は避ける）：
{existing_samples}"""

        def _keep(arr, _slot=slot):
            # ⚠️ 配列を1本につなげると短い1部目が200字超に戻る（2026-09-13 Sol指摘）。形が違う物を落とす
            kept = [x for x in arr if _valid_tree(x)]
            if len(kept) < len(arr):
                print(f"[generate] {_slot}: 形か長さが基準外 {len(arr) - len(kept)}本を除外"
                      f"（ちょうど2部・1部目{FIRST_PART_MIN}〜{FIRST_PART_MAX}字）")
            if facts:
                from botlib import judge_fact_violations
                checked = []
                skip_money = bool(facts.get("_金額は照合しない"))
                for x in kept:
                    why = judge_fact_violations(x, facts)
                    if why and skip_money and "金額" in why:
                        why = None      # 価格はご本人の判断。場所と営業時間だけ見る
                    if why:
                        print(f"[generate] {_slot}: 事実照合NGで除外 → {why}: {str(x)[:50]}")
                    else:
                        checked.append(x)
                kept = checked
            # 最終バリデーション: 空/短すぎ/薬機法NG語/プロンプト漏れ/ハングルを落とす最後の防波堤
            from botlib import validate_post_content
            out = []
            for x in kept:
                r = validate_post_content(x)
                if r:
                    print(f"[generate] {_slot}: 検証NGで除外 → {r}: {str(x)[:60]}")
                else:
                    out.append(x)
            return out

        try:
            new_posts, why = _generate_valid_posts(client, system_prompt, user_prompt,
                                               f"{account} {slot}", _keep)
        except _RefillTimeout as e:
            new_posts, why = [], f"期限切れ: {e}"
        if not new_posts:
            print(f"[generate] {account} {slot}: 補充できず → {why}")
            continue
        posts[slot].extend(new_posts)
        print(f"[generate] {account} {slot}: {len(new_posts)}本を追加（合計{len(posts[slot])}本）")
        generated_any = True
        filled_slots.append(slot)

    _disarm_deadline()
    if generated_any:
        _save_json_atomic(posts_path, posts)
        print(f"[generate] {posts_file} を更新しました")

    # 補充が必要だったのに1本も生成できなかった＝このままではプールが枯渇して
    # 投稿が止まる。exit 0 で静かに終わらず、異常終了してワークフローの通知に乗せる。
    # ⚠️ 全体で1枠でも成功すると通ってしまうと、朝夜の失敗に気づけない（Sol指摘）。
    # 補充が要る枠ごとに見る。
    missed = [s_ for s_ in needed_slots if s_ not in filled_slots]
    if missed:
        print(f"[generate] {account}: 補充できなかった枠 {', '.join(missed)} → 異常終了",
              file=sys.stderr)
        sys.exit(1)

    return generated_any


def _supabase_used_texts(salon_name: str) -> set:
    """このサロンで投稿済みの本文をすべて集める（SaaSモード用）。

    ⚠️ 件数だけで数えると、12本中12本使用済みでも
    `total - used % total` が 12 になり「残12本→補充不要」と誤判定する
    （2026-09-13 Sol指摘）。本文そのもので突き合わせる。"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set(), False
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        url = (f"{SUPABASE_URL}/rest/v1/salons"
               f"?salon_name={urllib.parse.quote('eq.' + salon_name)}&select=id&limit=1")
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15) as r:
            rows = json.loads(r.read())
        if not rows:
            return set(), False
        sid = rows[0]["id"]
        used, page, offset = set(), 1000, 0
        while True:
            if _time_left() < 30:   # 実行全体の期限を守る。読み切れなければ「不完全」扱い（安全側）
                print(f"[generate/saas] Supabase使用済み取得が期限内に終わらず（{salon_name}）→ 不完全として扱います")
                return used, False
            url = (f"{SUPABASE_URL}/rest/v1/post_logs?salon_id=eq.{sid}"
                   f"&select=post_content&order=id.asc&limit={page}&offset={offset}")
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
                chunk = json.loads(r.read())
            used.update(x.get("post_content") or "" for x in chunk)
            if len(chunk) < page:
                return used, True
            offset += page
    except Exception as e:
        # ⚠️ 読み切れなかったことを黙って隠すと、使用済み12本を「残12本・補充不要」と
        # 誤判定する（2026-09-13 Sol指摘）。取れた分と「不完全」を返す
        print(f"[generate/saas] Supabase使用済み取得エラー ({salon_name}／取れた分だけ使います): {e}")
        return locals().get("used", set()), False


def _supabase_used_count(salon_name: str, slot: str) -> int:
    """Supabaseのpost_logsから使用済み投稿数を取得（SaaSモード用・旧方式）"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    try:
        h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        url = (f"{SUPABASE_URL}/rest/v1/salons"
               f"?salon_name=eq.{urllib.parse.quote(salon_name)}&select=id&limit=1")
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=10) as r:
            rows = json.loads(r.read())
        if not rows:
            return 0
        salon_id = rows[0]["id"]
        url = (f"{SUPABASE_URL}/rest/v1/post_logs"
               f"?salon_id=eq.{salon_id}&slot=eq.{slot}&select=id")
        req = urllib.request.Request(url, headers={
            **h, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            cr = r.headers.get("Content-Range", "")
            return int(cr.split("/")[1]) if "/" in cr else 0
    except Exception as e:
        print(f"[generate/saas] Supabase使用数取得エラー ({salon_name}/{slot}): {e}")
        return 0


def generate_for_saas(salon_name: str, posts_file: str, rules_file: str):
    """SaaSモード: posts_saas/から読んでSupabaseで残数確認、生成してposts_saas/に書き戻す"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # 設定漏れ・ファイル欠落は「補充不能」なので静かに0で終わらない（2026-09-21 Sol指摘）
        print("[generate/saas] ANTHROPIC_API_KEY が未設定 → 補充不能・異常終了", file=sys.stderr)
        sys.exit(1)

    posts_path = os.path.join(_BASE, posts_file)
    try:
        posts = json.load(open(posts_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[generate/saas] {posts_file} を読めません → 補充不能・異常終了: {e}", file=sys.stderr)
        sys.exit(1)
    if not any(slot in posts for slot in ("morning", "noon", "evening")):
        print(f"[generate/saas] {posts_file} に morning/noon/evening の枠が無い → 補充不能・異常終了",
              file=sys.stderr)
        sys.exit(1)
    rules = _load_rules(rules_file)
    facts = _load_facts(salon_name)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    generated_any = False

    used_texts, used_complete = _supabase_used_texts(salon_name)

    def _key(p):
        return p if isinstance(p, str) else (p[0] if p else "")

    needed, filled, degraded = [], [], []
    for slot in ["morning", "noon", "evening"]:
        if slot not in posts:
            continue
        remaining = len([p for p in posts[slot] if _key(p) not in used_texts])
        # ⚠️ 数え切れていないときに「足りている」と判断しない。実際は0本でも
        # 「残12本→補充不要」になり、投稿側が過去投稿の再利用に入る
        if used_complete and remaining > THRESHOLD:
            print(f"[generate/saas] {salon_name} {slot}: 残{remaining}本 → 生成不要")
            continue
        if not used_complete:
            if len(posts[slot]) > THRESHOLD * 3:
                print(f"[generate/saas] {salon_name} {slot}: 使用済みを数え切れず、"
                      f"在庫は{len(posts[slot])}本あるので補充を見送ります（在庫十分とは区別・要確認）")
                degraded.append(slot)
                continue
            print(f"[generate/saas] {salon_name} {slot}: 使用済みを数え切れないため安全側に倒して補充します")
        needed.append(slot)

        print(f"[generate/saas] {salon_name} {slot}: 残{remaining}本 ≤ {THRESHOLD} → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7:30頃配信）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容が向く。",
            "noon": "昼投稿（12:00頃配信）。移動中・休憩中に読む人向け。共感しやすい体験談・サロンあるある・具体的な失敗談が向く。",
            "evening": "夜投稿（21:00頃配信）。1日の終わりに読む人向け。内省・本音・静かな気づき・今日学んだことが向く。",
        }[slot]

        existing_samples = "\n".join([
            str(posts[slot][i])[:80] for i in range(min(5, len(posts[slot])))
        ])

        # ⚠️ 朝・昼・夜すべてツリー。本文が短いほど見られるのが実測で分かったため
        # （2026-09-13。〜100字は200字〜の6〜10倍。朝・昼・夜すべてで一致）。
        # 以前は「ツリーは昼だけ」で、それが朝夜の表示回数を落としていた。
        output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は **ちょうど2要素の配列**（ツリー投稿）です。
["1部目の本文", "2部目の本文"]
※1部目末尾を「予告文」で終えること絶対禁止。「〜を書きます」「〜を話します」
  「〜をお伝えします」「〜を紹介します」「正直に書きます」など、内容を宣言する
  メタ発言は全てNG。話を始めてしまい、一番気になるところで止めること。
※**1部目は60〜100字**。ここがタイムラインに出る部分で、短いほど読まれる（実測）。
※2部目は120〜250字。

出力例:
[
  ["1部目フック。改行は\nで表現。", "2部目は答えから。"],
  ["別の1部目。", "別の2部目。"]
]

JSON配列以外の文字は一切出力しないでください。説明文も不要です。"""

        system_prompt = [
            {"type": "text", "text": f"""あなたはThreads投稿の専門家です。
以下のルールに厳密に従って、{salon_name}アカウントの投稿を生成してください。

=== 投稿生成ルール ===
{rules}""", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"""=== 時間帯の特性（{slot}） ===
{slot_hint}

=== 医療・成分・学術名称の取り扱い（最優先ルール）===
成分名・学術名称・数値データは、上記のサロン情報に明記されているもののみ使用すること。
サロン情報に記載のない成分名・効能・数値・学術名称を創作・推測して書くことは絶対禁止。

{output_format}"""},
        ]

        user_prompt = f"""{GENERATE_COUNT}本の投稿を生成してください。

必ず守ること：
- ギャップ投稿（期待と現実のズレ）を半数以上に入れる（「〇〇なのに△△」「〇〇じゃない、実は△△」）
- W型ストーリー（失敗→成功→失敗→成功）を複数本に使う
- 黒木さんの実体験を素材にする：素材は上記ルール内の「黒木さん自身のストーリー」「ギャップ一覧」に書かれているものだけを使う（このアカウントのテーマに無関係な素材は使わない）。時期・経緯・程度を盛らない・変えない（ルール内の⚠️厳守注記に従う）
- 同じ素材・同じ角度の投稿を繰り返さない（{GENERATE_COUNT}本全部違う切り口にする）
- **保存型投稿を1〜2本必ず含める**：「〇〇の3つの間違い」「チェックリスト」など。末尾に「スクショ保存しておくと便利です」を入れる
- **あるある共感型を1〜2本必ず含める**：ターゲットが「私のことだ」と感じる具体的な描写
- **返信誘導投稿を1〜2本必ず含める**：末尾に二択・共感確認・悩み募集のどれかを入れる
- ハッシュタグは絶対に付けない
- 絵文字は使わない
- 各投稿は独立して読めるものにする
- 1部目は必ずスクロールが止まる1行フックから始める

既存投稿のサンプル（この角度は避ける）：
{existing_samples}"""

        def _keep(arr, _slot=slot):
            # ⚠️ 配列を1本につなげると短い1部目が200字超に戻る（2026-09-13 Sol指摘）。形が違う物を落とす
            kept = [x for x in arr if _valid_tree(x)]
            if len(kept) < len(arr):
                print(f"[generate] {_slot}: 形か長さが基準外 {len(arr) - len(kept)}本を除外"
                      f"（ちょうど2部・1部目{FIRST_PART_MIN}〜{FIRST_PART_MAX}字）")
            if facts:
                from botlib import judge_fact_violations
                checked = []
                skip_money = bool(facts.get("_金額は照合しない"))
                for x in kept:
                    why = judge_fact_violations(x, facts)
                    if why and skip_money and "金額" in why:
                        why = None      # 価格はご本人の判断。場所と営業時間だけ見る
                    if why:
                        print(f"[generate] {_slot}: 事実照合NGで除外 → {why}: {str(x)[:50]}")
                    else:
                        checked.append(x)
                kept = checked
            # 最終バリデーション（非SaaSと同じ防波堤。SaaSだけ素通りしていた・2026-09-21 Sol指摘）
            from botlib import validate_post_content
            out = []
            for x in kept:
                r = validate_post_content(x)
                if r:
                    print(f"[generate/saas] {_slot}: 検証NGで除外 → {r}: {str(x)[:60]}")
                else:
                    out.append(x)
            return out

        try:
            new_posts, why = _generate_valid_posts(client, system_prompt, user_prompt,
                                               f"{salon_name} {slot}", _keep)
        except _RefillTimeout as e:
            new_posts, why = [], f"期限切れ: {e}"
        if not new_posts:
            print(f"[generate/saas] {salon_name} {slot}: 補充できず → {why}")
            continue
        posts[slot].extend(new_posts)
        print(f"[generate/saas] {salon_name} {slot}: {len(new_posts)}本追加（合計{len(posts[slot])}本）")
        generated_any = True
        filled.append(slot)

    _disarm_deadline()
    if generated_any:
        _save_json_atomic(posts_path, posts)
        print(f"[generate/saas] {posts_file} を更新しました")

    # ⚠️ 1枠でも成功すると全体が成功に見えてしまうと、朝夜の失敗に誰も気づかない。
    # 補充が要る枠のうち1本も足せなかった物があれば異常終了して通知に乗せる（Sol指摘）
    missed = [s_ for s_ in needed if s_ not in filled]
    if missed:
        print(f"[generate/saas] {salon_name}: 補充できなかった枠 {', '.join(missed)} → 異常終了",
              file=sys.stderr)
        sys.exit(1)
    if degraded:
        # Supabaseが読めず補充の要否を判断できなかった枠。在庫はあるが「正常」ではない
        print(f"[generate/saas] {salon_name}: 使用済みを数え切れず判断不能の枠 {', '.join(degraded)}"
              f" → 異常終了（Supabase接続を確認）", file=sys.stderr)
        sys.exit(1)

    return generated_any


if __name__ == "__main__":
    account = sys.argv[1] if len(sys.argv) > 1 else "bemolle"
    _arm_hard_deadline()

    if account == "bemolle":
        generate_for_account("bemolle", "posts.json", "used_posts.json", "GENERATE_RULES.md")
    elif account == "personal":
        generate_for_account("personal", "posts_personal.json", "used_posts_personal.json", "GENERATE_RULES_personal.md")
    elif account == "saas_bemolle":
        generate_for_saas("bemolle_diet", "posts_saas/posts_bemolle_diet.json", "GENERATE_RULES.md")
    elif account == "saas_personal":
        # Supabaseの実サロン名は aya_kuroki_0929。旧名 aya_0929_private のファイルに
        # 補充し続けて実プール(posts_aya_kuroki_0929.json)が枯渇していく事故の修正（2026-07-11）
        generate_for_saas("aya_kuroki_0929", "posts_saas/posts_aya_kuroki_0929.json", "GENERATE_RULES_personal.md")
    else:
        print(f"[generate] 不明なアカウント: {account}")
        sys.exit(1)
