import json, subprocess, os, sys

def merge_union(fname):
    r = subprocess.run(['git', 'show', 'FETCH_HEAD:' + fname],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(fname):
        return
    try:
        remote = json.loads(r.stdout)
        with open(fname) as f:
            local = json.load(f)
        if isinstance(remote, list) and isinstance(local, list):
            merged = local + [x for x in remote if x not in local]
        else:
            merged = {**remote, **local}
        with open(fname, 'w') as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f'[merge] {fname}: remote={len(remote)} local={len(local)} merged={len(merged)}')
    except Exception as e:
        print(f'[merge] {fname} error: {e}')

for fname in sys.argv[1:]:
    merge_union(fname)
