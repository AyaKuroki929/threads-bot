"""セッションJSONから storageState 形式を stdout に出力（GitHub Secret 更新用）。

使い方:
  python3 slim_session.py session.json > session_slim.json           # bemolle
  python3 slim_session.py session_personal.json > session_slim.json  # 個人
  python3 slim_session.py                                            # 引数なしは session_personal.json（互換）

⚠️ 実データは stdout に出力。ステータスメッセージは stderr に出すので、
   リダイレクトで session_slim.json に混入しない。
"""
import json
import sys

infile = sys.argv[1] if len(sys.argv) > 1 else "session_personal.json"
d = json.load(open(infile))
slim = {"cookies": d["cookies"], "origins": []}
json.dump(slim, sys.stdout, indent=2)
sys.stdout.write("\n")
print(f"OK {len(slim['cookies'])} 件（{infile} → stdout）", file=sys.stderr)
