#!/usr/bin/env python3
"""月曜21時の宣伝枠（個人アカ）の文章在庫を補充する。

未使用の在庫が2本を切ったら、Claudeで3本生成して promo_posts_personal.json に足す。
週1本消費なので、在庫4本＝約1ヶ月分。毎日走らせても在庫が足りていれば何もしない。
"""

import json
import os
import sys

import anthropic

BASE = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(BASE, "promo_posts_personal.json")
USED_FILE = os.path.join(BASE, "promo_used_personal.json")
MIN_STOCK = 2
GENERATE_N = 3
LINE_URL = "https://lin.ee/88vrtbQ"

PROMPT = f"""あなたは、Threads自動投稿サービス「とうこさん」を実際に使っている美容サロンオーナー本人として、Threadsの投稿を書きます。
読み手は、SNSが続かなくて困っている同業のサロンオーナーです。

# 最重要方針
- 主役は「サービスの機能」ではなく「使ったことで何が変わったか」。機能・価格は変化の理由として後ろに置く
- 読み手が欲しいのは自動投稿そのものではない。欲しいのは
  「投稿を考えなくてよくなる」「止まらずに続く」「Instagramを知ってもらう入口が増える」「施術中にSNSを気にしなくてよくなる」
- 冒頭は必ず、読み手の状況・悩み・疑いのどれかから入る。サービス名・価格・機能から始めることを禁止する

# 使ってよい事実（これ以外の数字・出来事・会話は書かない。1つでも創作したら不合格）
- Threadsに1日3回（朝7時・昼12時・夜9時）自動投稿する。文章はAIが作り、在庫の補充も自動
- 導入時の作業＝Googleフォームにサロン情報を記入＋連携リンクを開いて許可を1回押す。以降の作業はない
- 月2,750円（税込）・最低利用期間3ヶ月
- 画像は付けられない。ビフォーアフターや当日の告知は本人が手動投稿する必要がある
- 投稿者本人のThreadsは一文字も手書きしていない。直近30日の表示回数は合計32,020回・1日平均1,067回
- 現在12アカウントで稼働。直近27日で投稿が0件だった日は0日。1日30〜36件・合計1,000件
- 一番長く動いているアカウントは2026年5月19日から止まっていない
- サロンのInstagramは8日で1,113人から1,149人になった
  ※この数字を使うときは「同じ時期に広告も回しているのでThreadsだけの成果ではない」と必ず本文に書く
- 投稿者自身も、施術・片付け・発注で手一杯になり投稿が続かなかった

# 絶対に書いてはいけないこと
- 上の一覧に無い数字（「月50人増えた」等）・出来事・会話・お客様の声
- 実名、サロン名、アカウント名、地名などの特定できる情報
- 「絶対」「必ず」「100%」「確実」「保証」などの断定・効果の保証
- 「知る限りいちばん安い」などの比較優位の主張
- Instagramの増加をThreadsだけの成果として書くこと
- 「今始めないと手遅れ」のような煽り
- 導入手順を細かく説明して面倒に見せること（実際はフォーム記入と許可1回）
- 画像も含めて全部自動化できるように見せること

# 投稿の型（毎回どれか1つを選ぶ。前回と同じ型を続けない）
A 作業を手放した変化：投稿が続かなかった頃の場面 → 今は手書きゼロで動いている → 何が楽になったか
B 止まらない証明：「どうせ止まるでしょう」という疑い → 稼働記録の実測値 → できないこと（画像）も正直に
C Instagramの入口：Instagramを増やしたいがThreadsまで書けない → 表示回数の実測 → 広告併用を正直に明記
D 一部だけ手放す：全部任せるのが怖い気持ち → 手放したのは「考えて書く部分」だけ → 写真は自分で出す

# 文体
- 話し言葉のですます調。サロンオーナー仲間に話しかける温度
- 語尾の「ね」は1投稿に1回まで。「うち」は使わない。ダッシュ（——、—）は使わない
- AIが書いたような文章を禁止する：
  - 総括の一文で締める（「〜ということです」「〜だと思っています」で全体をまとめる）
  - 対句できれいに落とす（「Aではなく、Bでした」「時間も増えて、フォロワーも増えて」）
  - 決め台詞・気の利いた一文でオチをつける
  - 体言止めの多用、同じ語尾の3連続
- 教訓・まとめ・名言風の締めを書かない

# 構成
1行目：読み手の状況・悩み・疑いから入る（機能や価格から始めない）
本文：変化を具体的に。使う数字は上の一覧から必要な分だけ。全部並べない
締め：問い合わせる理由を投稿ごとに変える（「同じところで止まっている方は」「続かないのが悩みの方は」等）＋公式LINEからお問い合わせください
最終行：{LINE_URL}

# 形式
- 280〜450字（250字未満・451字以上は不合格）
- 段落を空行で分ける
- 最終行は必ず {LINE_URL} だけ

# すでに使った投稿（型・書き出し・使う数字を被らせない）
{{existing}}

上のルールで、新しい投稿を{GENERATE_N}本書いてください。3本は必ず違う型にすること。
出力は次のJSONだけ。説明文は書かないでください。
{{"posts": ["本文1", "本文2", "本文3"]}}
"""


# ── 採用検査（AIの自己申告を信用せず機械で弾く。2026-08-04追加）──────────
# 実名・サロン名（本人・クライアント・関係先）。1つでも入っていたら不採用。
NG_NAMES = [
    "ベモーレ", "bemolle", "黒木", "とうこさん公式", "ピッコロ", "piccolo",
    "つばめの巣", "アイリス", "ファミリエ", "うらかたさん", "ゆみか", "晶子",
    "杉村", "山内", "門馬", "瀧本", "中野", "湯目", "久保", "阿部", "月と雫",
]
# 誇大・断定表現
NG_WORDS = ["絶対", "必ず", "100%", "確実", "保証", "いちばん安い", "一番安い",
            "最安", "手遅れ", "今すぐ始めないと", "誰でも稼げる", "儲かります"]
# 本文に書いてよい数字（実測データ）。これ以外の3桁以上の数字が出たら不採用。
ALLOWED_NUMS = {
    "2750", "2,750", "3", "7", "12", "9", "30", "27", "36", "1000", "1,000",
    "32020", "32,020", "1067", "1,067", "1113", "1,113", "1149", "1,149",
    "2026", "5", "19", "1", "2", "8", "0", "450", "350",
}
# Instagram増加を語るときは「広告も併用しており Threads だけの成果ではない」の明記が必要。
# 単に「広告」の語があるだけでは不可（別の文脈で広告に触れているだけのことがあるため）。
IG_NUMS = ("1,113", "1113", "1,149", "1149")
AD_DISCLAIMER = ("だけの数字ではありません", "だけの成果ではありません",
                 "だけで増えた", "広告も回している", "広告も回しているので",
                 "広告も動かして", "広告と併用")


def reject_reason(p: str):
    """不採用の理由を返す（採用なら None）"""
    import re
    # 内容の検査を先に行う（長さで弾くと本当の問題が見えなくなるため）
    if not p.rstrip().endswith(LINE_URL):
        return "最終行がLINEリンクでない"
    for w in NG_NAMES:
        if w in p:
            return f"実名・固有名詞が含まれる（{w}）"
    for w in NG_WORDS:
        if w in p:
            return f"誇大・断定表現が含まれる（{w}）"
    # 数字チェック：URL部分を除いた本文の数字を見る
    body = p.replace(LINE_URL, "")
    for n in re.findall(r"[0-9][0-9,]*", body):
        if n not in ALLOWED_NUMS:
            return f"実データに無い数字（{n}）"
    if any(n in body for n in IG_NUMS) and not any(w in body for w in AD_DISCLAIMER):
        return "Instagramの数字を出しているのに広告併用の明記が無い"
    # 形式（長さ）は最後に見る。250〜450字（Threadsで読み切れる長さ）
    if len(p) < 250:
        return f"250字未満（{len(p)}字）"
    if len(p) > 450:
        return f"450字超（{len(p)}字）"
    return None


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    pool = _load(POOL_FILE, {})
    posts = pool.get("posts") or []
    used = set(_load(USED_FILE, []))
    unused = [p for p in posts if p not in used]

    print(f"[promo] 在庫: 未使用{len(unused)}本 / 全{len(posts)}本")
    if len(unused) >= MIN_STOCK and "--force" not in sys.argv:
        print("[promo] 在庫は足りています。生成しません")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[promo] ANTHROPIC_API_KEY が無いため生成できません")
        return 1

    existing = "\n\n---\n\n".join(posts[-6:]) if posts else "（まだありません）"
    prompt = PROMPT.replace("{existing}", existing)

    client = anthropic.Anthropic()
    last_err = None
    for model in ("claude-sonnet-5", "claude-sonnet-4-6"):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            # 先頭が thinking ブロックのことがあるため、text ブロックだけを拾う
            raw = "".join(b.text for b in resp.content
                          if getattr(b, "type", "") == "text").strip()
            if not raw:
                raise RuntimeError("応答にテキストが含まれていません")
            break
        except Exception as e:
            last_err = e
            print(f"[promo] {model} で生成失敗: {e}")
    else:
        raise RuntimeError(f"宣伝文の生成に失敗しました: {last_err}")

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[raw.find("{"):]
    new_posts = json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("posts") or []

    added = []
    for p in new_posts:
        p = p.strip()
        if not p or p in posts:
            continue
        reason = reject_reason(p)
        if reason:
            print(f"[promo] 不採用: {reason}")
            continue
        posts.append(p)
        added.append(p)

    if not added:
        raise RuntimeError("採用できる宣伝文が1本もありませんでした")

    pool["posts"] = posts
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    print(f"[promo] {len(added)}本を追加しました（在庫 未使用{len(unused) + len(added)}本）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
