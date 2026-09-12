"""安全装置を1つずつ壊して、テストがちゃんと落ちるかを確かめる（変異テスト）。

通るだけの点検は無意味なので、**わざと壊して検知するか**まで機械で確認する。
ここが「素通り」になったら、そのテストは安全装置を守っていない
（2026-09-12 Sol指摘：締切確認を外しても38シナリオ全部が通ってしまった）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(BASE, "post_saas.py")
SUITE = os.path.join(BASE, "tests", "test_post_resilience.py")

REVERT = (
    "            if not carried:\n"
    "                try:\n"
    "                    row = post_state.set_part(row, i, lost_response=False)\n"
    "                    lost_response = False\n"
    "                except Exception as ex:\n"
    '                    print(f"[state] 未送信の印戻しに失敗（安全側で不明のまま）: {str(ex)[:80]}")\n'
    "            break"
)
REPAIR_PUBLISHED = (
    '        if st == "PUBLISHED":\n'
    "            try:\n"
    "                row = post_state.set_part(row, 0, status=post_state.PART_PUBLISHED)"
)

# ⚠️ 二重に守っている箇所（本文ハッシュの照合など）は、片方だけ壊しても
# もう片方が止めるため、ここには単体では載せない。代わりに、
# 一番外側の入口（_ledger_consistent / _all_parts_published）を壊して検知させる。
#
# (名前, 元のコード, 壊したコード)
MUTATIONS = [
    ("送信直前の締切確認",
     'if _out_of_time(f"{label}の公開要求"):',
     'if False and _out_of_time(f"{label}の公開要求"):'),
    ("未送信のときに『結果不明』の印を戻す", REVERT, "            break"),
    ("締切中断の印戻しは「今回立てた印」限定",
     "            if not carried:\n                try:\n"
     "                    row = post_state.set_part(row, i, lost_response=False)",
     "            if True:\n                try:\n"
     "                    row = post_state.set_part(row, i, lost_response=False)"),
    ("持ち越した未確定を今回の400で消さない（carried）",
     "if definitive_refusal and not carried:", "if definitive_refusal:"),
    ("登録アカウントと実アカウントの照合",
     "            if mismatch:\n                raise RuntimeError(",
     "            if False:\n                raise RuntimeError("),
    ("台帳に固定した投稿アカウントの照合",
     "    if owner and str(owner) != str(user_id):",
     "    if False and str(owner) != str(user_id):"),
    ("ERROR/EXPIREDでも応答喪失後は作り直さない",
     'if st in ("ERROR", "EXPIRED") and not lost_response:',
     'if st in ("ERROR", "EXPIRED"):'),
    ("投稿を始めたアカウントを台帳に固定保存する",
     "            row = post_state.update(row, publisher_user_id=str(user_id))",
     "            row = row"),
    ("投稿履歴があるのに投稿者不明なら止める",
     "        if any((p.get(\"creation_id\") or p.get(\"post_id\")",
     "        if False and any((p.get(\"creation_id\") or p.get(\"post_id\")"),
    ("記録復旧の失敗を完了にしない",
     "        _state_finish(row,\n"
     "                      post_state.STATUS_PUBLISHED if finish_status else post_state.STATUS_ATTENTION,",
     "        _state_finish(row,\n"
     "                      finish_status or post_state.STATUS_ATTENTION,"),
    ("添字の一意と連続の確認",
     "    if sorted(idx) != list(range(len(texts))):",
     "    if False and sorted(idx) != list(range(len(texts))):"),
    ("ハッシュの存在も必須にする",
     "        if p.get(\"hash\") != post_state.part_hash(t):",
     "        if p.get(\"hash\") and p[\"hash\"] != post_state.part_hash(t):"),
    ("残り0秒は時間切れ扱い",
     "        if d is not None and now >= d:",
     "        if d is not None and now > d:"),
    ("台帳の整合チェック（投稿前）",
     "    bad = _ledger_consistent(row, texts)\n    if bad:",
     "    bad = None\n    if bad:"),
    ("記録復旧での本文一致確認",
     "    if st0 == post_state.PART_PUBLISHED and text and (\n"
     '            first.get("hash") != post_state.part_hash((payload.get("texts") or [text])[0])',
     "    if False and st0 == post_state.PART_PUBLISHED and text and (\n"
     '            first.get("hash") != post_state.part_hash((payload.get("texts") or [text])[0])'),
    ("コンテナ状態照会の401をトークン切れにする",
     '            raise TokenExpiredError(f"トークン切れ HTTP {e.code}（コンテナ状態の問い合わせ）")',
     '            return None'),
    ("回収完了後の last_run 同期",
     '                        _sync_last_run(salon["salon_name"], slot, jst_date=jst_date)\n'
     "                        continue",
     "                        continue"),
    ("通常経路・全公開済みの last_run 同期",
     "                    _sync_last_run(salon_name, SLOT, jst_date=jst_date)\n"
     '                    results["ok"].append(salon_name)\n'
     "                    continue",
     '                    results["ok"].append(salon_name)\n'
     "                    continue"),
    ("履歴が先頭から連続していることの確認",
     '            return (f"パート{i}が公開済み・投稿IDありになっていないのに、"\n'
     '                    f"{i+1}部目の履歴があります")',
     "            pass"),
    ("pendingなのに公開履歴がある台帳を止める",
     '            return f"パート{i+1}は未処理の印なのに、公開の履歴が残っています"',
     "            pass"),
    ("回収の行ごとの例外分離",
     "            except Exception as e:\n"
     '                print(f"[recover] {op_id} の処理で想定外のエラー: {str(e)[:150]}")',
     "            except SystemExit as e:\n"
     '                print(f"[recover] {op_id} の処理で想定外のエラー: {str(e)[:150]}")'),
    ("last_run の過去日ガード",
     "    if jst_date is not None and str(jst_date) != today:",
     "    if False and str(jst_date) != today:"),
    ("原文ハッシュの固定保存",
     '                            extra["original_hash"] = post_state.part_hash(original_first)',
     "                            pass"),
    ("記録の直前で原文を照合する（通常経路）",
     "        bad_original = _original_mismatch(row, original_first)\n        if bad_original:",
     "        bad_original = None\n        if bad_original:"),
    ("回収失敗のまとめ通知",
     "        sent = _notify_line(",
     "        sent = (lambda *a, **k: True)("),
    ("通常投稿成功時の last_run 同期",
     '        _sync_last_run(salon_name, slot, jst_date=row.get("jst_date"))\n'
     '        print(f"[OK] {salon_name}: post_id={post_id}")',
     '        print(f"[OK] {salon_name}: post_id={post_id}")'),
    ("記録復旧で宣伝の使用済みも戻す",
     "        if _promo_pending(row):",
     "        if False and _promo_pending(row):"),
    ("宣伝の使用済み記録も原文照合を通す",
     '    if promo and res["complete"] and not _original_mismatch(row, original_first):',
     '    if promo and res["complete"]:'),
    ("原文ハッシュを上書きしない",
     '                        if i == 0 and original_first is not None and not p.get("original_hash"):',
     "                        if i == 0 and original_first is not None:"),
    ("同じ理由の再通知を抑える",
     "    if mark in prev:",
     "    if False and mark in prev:"),
    ("通知済み印を消さない",
     "        if RECOVER_NOTE_MARK not in note:\n            note = note + mark",
     "        pass"),
    ("通知は送れてから通知済みにする",
     "        if sent:\n            _mark_notified(failures)",
     "        _mark_notified(failures)\n        if sent:\n            pass"),
    ("失敗の種類で通知を識別する",
     "    mark = RECOVER_NOTE_MARK + kind",
     '    mark = RECOVER_NOTE_MARK + "same"'),
    ("宣伝の使用済みを別の完了条件にする",
     '    if res["complete"] and logged and not _promo_pending(row):',
     '    if res["complete"] and logged:'),
    ("宣伝の使用済み失敗を回収に残す",
     "            if not _mark_promo_done(row, text):",
     "            if False and not _mark_promo_done(row, text):"),
    ("記録復旧でFINISHEDを公開済み扱いしない",
     REPAIR_PUBLISHED,
     REPAIR_PUBLISHED.replace('if st == "PUBLISHED":', 'if st in ("PUBLISHED", "FINISHED"):')),
]


def main() -> int:
    original = open(TARGET, encoding="utf-8").read()
    backup = tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding="utf-8")
    backup.write(original)
    backup.close()
    bad = []
    try:
        for name, before, after in MUTATIONS:
            if before not in original:
                print(f"❌ {name}: 対象のコードが見つかりません（実装が変わった？）")
                bad.append(name)
                continue
            open(TARGET, "w", encoding="utf-8").write(original.replace(before, after, 1))
            r = subprocess.run([sys.executable, SUITE], capture_output=True, text=True)
            out = r.stdout + r.stderr
            # ⚠️「終了コードが非0」だけでは、構文エラーやimport失敗でも合格になる。
            # 判定(❌)が実際に落ちたことまで確かめる
            if "❌" in out:
                failed = [l.strip() for l in out.splitlines() if "❌" in l][:2]
                print(f"✅ {name}: 壊すとテストが落ちます（{' / '.join(failed)}）")
            elif r.returncode == 0:
                print(f"🚨 {name}: 壊してもテストが通ってしまいました")
                bad.append(name)
            else:
                tail = (out.strip().splitlines() or ["(出力なし)"])[-1]
                print(f"🚨 {name}: テストは落ちたが判定の失敗ではない（{tail[:80]}）")
                bad.append(name)
    finally:
        shutil.copy(backup.name, TARGET)
        os.unlink(backup.name)

    if bad:
        print(f"\n🚨 {len(bad)}件の安全装置がテストで守られていません")
        return 1
    print(f"\n✅ {len(MUTATIONS)}件の安全装置は、壊すとテストが落ちます")
    return 0


if __name__ == "__main__":
    sys.exit(main())
