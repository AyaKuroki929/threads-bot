"""失敗強化版ルールの試し生成（プレビュー専用）。
ファイルへ書き込まず・コミットせず・ログに出力するだけ。本番プールは汚さない。
Usage: python3 preview_gen.py [salon_name]   # 既定 urakata_san_official
B2B（うらかた）はシート不要・Supabase不要。ANTHROPIC_API_KEY だけで動く。"""
import sys, json
import anthropic
from generate_saas_posts import b2b_to_rules, load_local_salons, normalize_threads_id

target = sys.argv[1] if len(sys.argv) > 1 else "urakata_san_official"
salon = next(
    (s for s in load_local_salons()
     if normalize_threads_id(s.get("Threadsのアカウント名（@から始まるID）", "")) == target
     or s.get("サロン名") == target),
    None,
)
if not salon:
    print(f"[preview] サロンが見つかりません: {target}")
    sys.exit(1)

rules = b2b_to_rules(salon)
client = anthropic.Anthropic()


def gen(slot, n, tree=False):
    if tree:
        fmt = '各要素は2要素の配列（ツリー2部構成）。改行は\\nで表現。例: [["1部フック\\n途中で止める", "2部は答えから"]]'
        lenrule = "各部150〜300字"
    else:
        fmt = '各要素は単発投稿の文字列。改行は\\nで表現。例: ["投稿1の本文", "投稿2の本文"]'
        lenrule = "各投稿200〜300字・最長350字"
    hint = {"morning": "朝。前向きな気づき・軽い問いかけ",
            "noon": "昼。共感・保存したくなる知識",
            "evening": "夜。内省・本音"}[slot]
    system = f"""あなたはSNS投稿の専門家です。以下のルールに従ってThreads投稿を生成してください。
=== サロン情報・投稿ルール ===
{rules}
=== 時間帯の特性 ===
{hint}
=== 出力形式（厳守）===
JSON配列だけを返す。{fmt}
JSON配列以外は一切出力しない。"""
    user = f"""{n}本の{slot}投稿を生成してください。
- 1行目は必ずスクロールが止まる強いフック
- 失敗エピソード起点を6〜7割、必ずW型（失敗→気づき→今）で着地
- 失敗の種類を散らす（お金/時間/人/思い込み/道具）
- このアカウント固有の口癖・コンセプトを自然に反映
- ハッシュタグ禁止
- {lenrule}
JSON配列で{n}本すべて返す。"""
    resp = client.messages.create(model="claude-sonnet-4-6", max_tokens=4000,
                                  system=system, messages=[{"role": "user", "content": user}])
    raw = resp.content[0].text.strip()
    s, e = raw.find("["), raw.rfind("]") + 1
    try:
        return json.loads(raw[s:e])
    except Exception as ex:
        print(f"[preview] パース失敗({slot}): {ex}\n{raw[:400]}")
        return []


print("################ PREVIEW: うらかた 失敗強化版（本番未投入）################")
for slot, n, tree in [("morning", 4, False), ("noon", 2, True), ("evening", 4, False)]:
    print(f"\n================= {slot} =================")
    for i, p in enumerate(gen(slot, n, tree), 1):
        if isinstance(p, list):
            print(f"[{i}] （ツリー）")
            for j, part in enumerate(p, 1):
                print(f"  {j}部: {part}")
        else:
            print(f"[{i}] {p}")
        print()
print("################ END PREVIEW ################")
