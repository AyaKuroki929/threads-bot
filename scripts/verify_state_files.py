#!/usr/bin/env python3
"""状態ファイル（last_run / last_posted など）が壊れていないか確かめる最後の砦。

2026-11-17 21:17 JST、投稿の記録 last_run.json に競合マーカー
（<<<<<<< Updated upstream）が入ったまま commit・push され、以後すべての実行で
読み込みに失敗して🚨が鳴り続けた。原因は `git pull --rebase --autostash` が
未コミットの last_run を退避→適用時に競合させ、その状態を `git add` が
そのまま拾ったこと。

ここで止めれば、壊れた内容は GitHub に乗らない。
使い方:
  python3 scripts/verify_state_files.py a.json b.json        # 手元のファイルを見る
  python3 scripts/verify_state_files.py --head a.json b.json # これから push する中身（HEAD）を見る
"""
import json
import subprocess
import sys

MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def body(path, head):
    if not head:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None
    r = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main(argv):
    head = "--head" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print("使い方: verify_state_files.py [--head] <file> ...", file=sys.stderr)
        return 2
    where = "これから push する中身" if head else "手元のファイル"
    bad = []
    for p in paths:
        text = body(p, head)
        if text is None:
            continue  # まだ無いファイルは対象外（新規アカウント追加時など）
        hit = [l for l in text.splitlines() if l.startswith(MARKERS)]
        if hit:
            bad.append(f"{p}: 競合マーカーが入っている（{hit[0][:30]}）")
            continue
        try:
            json.loads(text)
        except Exception as e:
            bad.append(f"{p}: JSONとして読めない（{type(e).__name__}: {e}）")
    if bad:
        print(f"[verify_state] 🚨 {where}が壊れています。push しません:", file=sys.stderr)
        for b in bad:
            print("  - " + b, file=sys.stderr)
        return 1
    print(f"[verify_state] OK（{where}・{len(paths)}件）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
