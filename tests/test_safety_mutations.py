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
TARGET_STATE = os.path.join(BASE, "post_state.py")
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

# ⚠️ 二重に守っている箇所（本文ハッシュの照合、_safe_fetch と行ごとの例外分離、
# _settle_if_notified と _mark_notified の二段構えなど）は、
# 片方だけ壊しても
# もう片方が止めるため、ここには単体では載せない。代わりに、
# 一番外側の入口（_ledger_consistent / _all_parts_published）を壊して検知させる。
#
# (名前, 元のコード, 壊したコード[, 対象ファイル])
MUTATIONS = [
    ("DBからの宣伝使用済み確認",
     "    used_db = _promo_used_from_db(salon_id)",
     "    used_db = set()"),
    ("使用済みを確認できないときは選ばない",
     '        raise PromoCheckFailed(str(e)[:120])',
     "        return set()"),
    ("通知は本文に載せた分だけ通知済みにする",
     "            _mark_notified(shown)",
     "            _mark_notified(failures)"),
    ("回収対象は状態だけで絞る（Python側で後から落とさない）",
     '        "status": f"in.({\',\'.join(live)})",',
     '        "status": f"in.({STATUS_UNKNOWN})",', TARGET_STATE),
    ("記録の修復が残る停止(hold_repair)を回収する",
     "    if st == STATUS_HOLD_REPAIR:",
     "    if False and st == STATUS_HOLD_REPAIR:", TARGET_STATE),
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
     "                      post_state.STATUS_PUBLISHED if finish_status\n"
     "                      else post_state.STATUS_HOLD_REPAIR,",
     "        _state_finish(row,\n"
     "                      finish_status or post_state.STATUS_HOLD_REPAIR,"),
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
     "                        if _state_finish(row, post_state.STATUS_LOGGED):\n"
     '                            _sync_last_run(salon["salon_name"], slot, jst_date=jst_date)',
     "                        if _state_finish(row, post_state.STATUS_LOGGED):\n"
     "                            pass"),
    ("通常経路・全公開済みの last_run 同期",
     "                    if _state_finish(row, post_state.STATUS_LOGGED):\n"
     "                        _sync_last_run(salon_name, SLOT, jst_date=jst_date)",
     "                    if _state_finish(row, post_state.STATUS_LOGGED):\n"
     "                        pass"),
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
    ("宣伝の使用済み記録の原文照合（入口で集約）",
     "    bad = _original_mismatch(row, text)\n    if bad:\n"
     '        print(f"[promo] 使用済みにしません: {bad}")\n        return False',
     "    bad = None\n    if bad:\n"
     '        print(f"[promo] 使用済みにしません: {bad}")\n        return False'),
    ("原文ハッシュを上書きしない",
     '                        if i == 0 and original_first is not None and not p.get("original_hash"):',
     "                        if i == 0 and original_first is not None:"),
    ("同じ種類の再通知を抑える",
     "    if kind in _notified_kinds(row):",
     "    if False and kind in _notified_kinds(row):"),
    ("通知済み印を消さない",
     "        if RECOVER_NOTE_MARK not in note:\n            note = note + mark",
     "        pass"),
    ("通知は送れてから通知済みにする",
     "        if sent:\n            # ⚠️ 本文に載せた分だけ通知済みにする",
     "        if True:\n            # ⚠️ 本文に載せた分だけ通知済みにする"),
    ("通知済みの種類は足していく（上書きしない）",
     "            kinds = _notified_kinds(cur) | {f[\"kind\"]}",
     "            kinds = {f[\"kind\"]}"),
    ("宣伝の使用済みを別の完了条件にする",
     '    if res["complete"] and logged and not _promo_pending(row):',
     '    if res["complete"] and logged:'),
    ("宣伝の使用済み失敗を回収に残す",
     "            if not _mark_promo_done(row, text):",
     "            if False and not _mark_promo_done(row, text):"),
    ("未確定の台帳に残る宣伝文も候補から外す",
     '            if pl.get("promo") and txt:\n                used.add(post_state.norm_text(txt))',
     "            pass"),
    ("人待ちへ移すのは知らせが送れたときだけ",
     "    if _notify_line(message):",
     "    if True or _notify_line(message):"),
    ("別実行が状態を変えた行を上書きしない",
     '                if fresh.get("status") != row.get("status"):',
     "                if False:"),
    ("通常経路でも記録が戻せるうちは人待ちにしない",
     "        fresh = _safe_fetch(row.get(\"op_id\"), row) or row\n        if _repairable(fresh):",
     "        fresh = _safe_fetch(row.get(\"op_id\"), row) or row\n        if False:"),
    ("本文ハッシュの一致確認（修復可否）",
     "    if first.get(\"hash\") != post_state.part_hash(body):\n        return False",
     "    if False:\n        return False"),
    ("原文ハッシュの一致確認（修復可否）",
     "    if _original_mismatch(row, text):\n        return False\n    return True",
     "    if False:\n        return False\n    return True"),
    ("メモの追記で通知済みの印を壊さない",
     "        head, mark = note.split(RECOVER_NOTE_MARK, 1)\n        return head + extra + RECOVER_NOTE_MARK + mark",
     "        pass"),
    ("日付跨ぎで翌日の枠を作らない（対象日の固定）",
     '            jst_date = _run_jst_date or datetime.now(JST).strftime("%Y-%m-%d")\n'
     "            action, row = _acquire_with_retry(salon_id, jst_date, SLOT)",
     '            jst_date = datetime.now(JST).strftime("%Y-%m-%d")\n'
     "            action, row = _acquire_with_retry(salon_id, jst_date, SLOT)"),
    ("未投稿・確認不能を台帳に残す",
     '            post_state._req("POST", post_state.TABLE, body={\n'
     '                "op_id": op_id, "salon_id": salon["id"], "jst_date": jst_date,',
     '            {} and post_state._req("POST", post_state.TABLE, body={\n'
     '                "op_id": op_id, "salon_id": salon["id"], "jst_date": jst_date,'),
    ("failed の行も知らせる対象へ移す",
     "        if row.get(\"status\") == post_state.STATUS_FAILED:",
     "        if False:"),
    ("過去日の未投稿も点検する",
     "    dates = [(base - timedelta(days=n)).strftime(\"%Y-%m-%d\") for n in range(days)]",
     "    dates = [base.strftime(\"%Y-%m-%d\")]"),
    ("台帳に残せなければ成功にしない",
     '        raise RuntimeError(f"取りこぼしを台帳に残せませんでした（{len(unflagged)}件）")',
     "        pass"),
    ("実行中に日付が変わったら新しい投稿をしない",
     "                        if _date_rolled_over():",
     "                        if False and _date_rolled_over():"),
    ("穴埋め側のアカウント照合",
     '            if salon.get("threads_user_id") and str(salon["threads_user_id"]) != str(user_id):\n'
     "                failed.append(f\"{salon['salon_name']}({slot})（アカウント不一致）\")",
     '            if False:\n'
     "                failed.append(f\"{salon['salon_name']}({slot})（アカウント不一致）\")"),
    ("調べられなかったことを知らせる",
     '        _notify_line("⚠️ とうこさん：投稿の取りこぼしを調べられませんでした。\\n"',
     '        print("⚠️ とうこさん：投稿の取りこぼしを調べられませんでした。\\n"'),
    ("時間帯を過ぎた実行でも穴を埋める",
     "        if win is not None and jst_hour not in win:\n"
     '            print(f"[LATE] {SLOT} の時間帯を過ぎています（現在 {jst_hour}時JST）"',
     "        if win is not None and jst_hour not in win:\n"
     "            return\n"
     '            print(f"[LATE] {SLOT} の時間帯を過ぎています（現在 {jst_hour}時JST）"'),
    ("まだ来ていないスロットを先出ししない",
     "        if win is not None and jst_hour < win.start:",
     "        if False and jst_hour < win.start:"),
    ("1つ前のスロットの取りこぼしを埋める",
     "            check_previous_slot(salons)",
     "            pass"),
    ("記録に必要な本文がそろっているかまで見る",
     "    payload = row.get(\"payload\") or {}\n"
     '    texts = payload.get("texts") or []',
     "    return True\n    payload = row.get(\"payload\") or {}\n"
     '    texts = payload.get("texts") or []'),
    ("コンテナ照会の403もトークン切れにする",
     '        if e.code in (401, 403):\n            raise TokenExpiredError(f"トークン切れ HTTP {e.code}（コンテナ状態の問い合わせ）")',
     '        if e.code == 401:\n            raise TokenExpiredError(f"トークン切れ HTTP {e.code}（コンテナ状態の問い合わせ）")'),
    ("宣伝復旧でも完了印の保存を確認する",
     '                        if _mark_promo_done(row, pl.get("original_first") or "") \\\n'
     '                                and _state_finish(_safe_fetch(op_id, row),',
     '                        if _mark_promo_done(row, pl.get("original_first") or "") \\\n'
     '                                or _state_finish(_safe_fetch(op_id, row),'),
    ("記録が戻せるうちは人待ちにしない",
     "                    and not _repairable(cur):\n"
     '                fields["status"] = post_state.STATUS_ATTENTION',
     "                    and True:\n"
     '                fields["status"] = post_state.STATUS_ATTENTION'),
    ("投稿を止めても公開済みの記録は戻す",
     "                        if _repairable(cur):\n"
     "                            _repair_safe(cur, salon, slot, jst_date, quiet=True)",
     "                        if False:\n"
     "                            _repair_safe(cur, salon, slot, jst_date, quiet=True)"),
    ("コンテナが消えた枠は人へ渡す",
     '        elif st in ("EXPIRED", "ERROR"):',
     "        elif False:"),
    ("完了印を残せなければ成功と言わない",
     "        if not _state_finish(row, post_state.STATUS_LOGGED):",
     "        if False and not _state_finish(row, post_state.STATUS_LOGGED):"),
    ("知らせが届いてから人待ちへ移す（まとめ通知側）",
     '            if f["kind"] in HUMAN_KINDS \\',
     "            if False \\"),
    ("使用済みを確認できないときの通常投稿への切替",
     "            except PromoCheckFailed as e:",
     "            except KeyError as e:"),
    ("記録復旧でFINISHEDを公開済み扱いしない",
     REPAIR_PUBLISHED,
     REPAIR_PUBLISHED.replace('if st == "PUBLISHED":', 'if st in ("PUBLISHED", "FINISHED"):')),
]


def main() -> int:
    originals = {f: open(f, encoding="utf-8").read() for f in (TARGET, TARGET_STATE)}
    bad = []
    try:
        for mut in MUTATIONS:
            name, before, after = mut[0], mut[1], mut[2]
            target = mut[3] if len(mut) > 3 else TARGET
            src = open(target, encoding="utf-8").read()
            if before not in src:
                print(f"❌ {name}: 対象のコードが見つかりません（実装が変わった？）")
                bad.append(name)
                continue
            open(target, "w", encoding="utf-8").write(src.replace(before, after, 1))
            try:
                r = subprocess.run([sys.executable, SUITE], capture_output=True, text=True)
            finally:
                # ⚠️ 1件ごとに必ず戻す。戻さないと変異が積み重なって結果が読めない
                open(target, "w", encoding="utf-8").write(originals[target])
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
        for f, text in originals.items():
            open(f, "w", encoding="utf-8").write(text)

    if bad:
        print(f"\n🚨 {len(bad)}件の安全装置がテストで守られていません")
        return 1
    print(f"\n✅ {len(MUTATIONS)}件の安全装置は、壊すとテストが落ちます")
    return 0


if __name__ == "__main__":
    sys.exit(main())
