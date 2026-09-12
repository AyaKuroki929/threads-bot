"""post_saas.py を本物のコードのまま動かすための偽サーバ（Threads API + Supabase）。
通信層(urllib.request.urlopen)だけ差し替える。分岐や判定は本物を通る。"""
import io, json, re, itertools, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

class Resp(io.BytesIO):
    """本物の http.client.HTTPResponse に合わせる。status が無いと
    supabase_post() が AttributeError になり、テストが甘くなる（Sol指摘#8）。"""
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status
        self.code = status
    def getcode(self): return self.status
    def __enter__(self): return self
    def __exit__(self, *a): return False

class World:
    def __init__(self):
        self.containers = {}          # cid -> {"status":..., "text":..., "reply_to":...}
        self.posts = []               # [{"id","text","timestamp"}]
        self.attempts = {}            # op_id -> row
        self.post_logs = []
        self.salons = []
        self.calls = {"create": 0, "publish": 0, "status": 0, "list": 0, "log_insert": 0}
        self._cid = itertools.count(1)
        self._pid = itertools.count(1000)
        self._row = itertools.count(1)
        # 挙動フック（シナリオごとに差し替える）
        self.publish_behavior = lambda cid, n: "ok"   # "ok"|"timeout"|"http500"
        self.status_override = None                    # cid -> 返す status
        self.list_behavior = "normal"                  # "normal"|"loop"|"empty"
        self.log_insert_behavior = lambda n: "ok"      # "ok"|"fail"|"fail_but_saved"
        self.publish_n = 0
        self.log_n = 0
        self.now = datetime.now(timezone.utc)
        self.db_clock_offset = 0.0   # テストで「時間が経った」ことにするための調整
        self.attempts_patch_behavior = None
        self.log_fk_violation = False
        self.me_behavior = None      # /me の挙動（"ok"/"401"/"timeout"/"empty"/実ID）
        self.publish_times = []      # 公開要求を送った時刻（締切超過の検査に使う）
        self.now_fn = None
        # 通信1回あたりに進む時間（秒）。テストの仮想時計と組み合わせて
        # 「待機だけでなく通信時間も持ち時間を食う」を再現する
        self.latency = 0.0
        self.on_latency = None

    def db_now(self):
        return (datetime.now(timezone.utc) + timedelta(seconds=self.db_clock_offset)).isoformat()

    # ---------- Threads ----------
    def th_create(self, params):
        self.calls["create"] += 1
        cid = f"CONTAINER_{next(self._cid)}"
        self.containers[cid] = {"status": "FINISHED", "text": params.get("text", [""])[0],
                                "reply_to": (params.get("reply_to_id") or [None])[0]}
        return {"id": cid}

    def th_publish(self, params):
        self.calls["publish"] += 1
        if self.now_fn:
            self.publish_times.append(self.now_fn())
        self.publish_n += 1
        cid = params["creation_id"][0]
        c = self.containers.get(cid)
        if c is None:
            raise urllib.error.HTTPError("u", 400, "no container", {}, io.BytesIO(b'{"error":"bad"}'))
        beh = self.publish_behavior(cid, self.publish_n)
        if beh == "ok":
            if c["status"] == "PUBLISHED":
                raise urllib.error.HTTPError("u", 400, "already", {},
                    io.BytesIO(b'{"error":{"message":"media already published"}}'))
            c["status"] = "PUBLISHED"
            pid = f"POST_{next(self._pid)}"
            c["post_id"] = pid
            self.posts.insert(0, {"id": pid, "text": c["text"], "reply_to": c.get("reply_to"),
                                  "timestamp": self.now.strftime("%Y-%m-%dT%H:%M:%S+0000")})
            return {"id": pid}
        if beh == "published_then_timeout":
            # サーバ側では公開されたが応答が失われた
            if c["status"] != "PUBLISHED":
                c["status"] = "PUBLISHED"
                pid = f"POST_{next(self._pid)}"
                c["post_id"] = pid
                self.posts.insert(0, {"id": pid, "text": c["text"], "reply_to": c.get("reply_to"),
                                      "timestamp": self.now.strftime("%Y-%m-%dT%H:%M:%S+0000")})
            raise TimeoutError("timed out")
        if beh == "http500":
            raise urllib.error.HTTPError("u", 500, "ISE", {}, io.BytesIO(b'{"error":"ise"}'))
        if beh == "http400":
            # サーバがはっきり断った＝この要求では公開されていない（応答は失われていない）
            raise urllib.error.HTTPError("u", 400, "Bad Request", {},
                                         io.BytesIO(b'{"error":{"message":"invalid container"}}'))
        raise TimeoutError("timed out")

    def th_status(self, cid):
        self.calls["status"] += 1
        if self.status_override is not None:
            v = self.status_override(cid) if callable(self.status_override) else self.status_override
            if v == "__fail__":
                raise urllib.error.HTTPError("u", 500, "ISE", {}, io.BytesIO(b'{}'))
            if v == "__401__":
                raise urllib.error.HTTPError("u", 401, "unauthorized", {}, io.BytesIO(b'{}'))
            return {"status": v} if v else {}
        c = self.containers.get(cid)
        return {"status": c["status"]} if c else {}

    def th_list(self, params):
        self.calls["list"] += 1
        if self.list_behavior == "empty":
            return {"data": [], "paging": {}}
        if self.list_behavior == "loop":
            return {"data": self.posts[:25], "paging": {"cursors": {"after": "SAME"}}}
        after = (params.get("after") or [None])[0]
        start = int(after) if after else 0
        limit = int((params.get("limit") or ["25"])[0])
        chunk = self.posts[start:start + limit]
        paging = {}
        if start + limit < len(self.posts):
            paging = {"cursors": {"after": str(start + limit)}}
        return {"data": chunk, "paging": paging}

    # ---------- Supabase ----------
    def sb(self, method, path, query, body):
        table = path
        q = urllib.parse.parse_qs(query)
        def eqval(k):
            v = q.get(k, [None])[0]
            return v.split(".", 1)[1] if v and "." in v else v
        if table == "salons":
            if method == "PATCH":
                self.calls["salons_patch"] = self.calls.get("salons_patch", 0) + 1
                return []
            return self.salons
        if table == "post_logs":
            if method == "GET":
                sid = eqval("salon_id"); slot = eqval("slot")
                rows = [r for r in self.post_logs
                        if (sid is None or r.get("salon_id") == sid)
                        and (slot is None or r.get("slot") == slot)]
                # op_id=eq.X / op_id=is.null を本物どおり効かせる
                opq = q.get("op_id", [None])[0]
                if opq == "is.null":
                    rows = [r for r in rows if r.get("op_id") in (None, "")]
                elif opq and opq.startswith("eq."):
                    rows = [r for r in rows if r.get("op_id") == opq[3:]]
                gte = q.get("posted_at", [None])[0]
                if gte and gte.startswith("gte."):
                    rows = [r for r in rows if str(r.get("posted_at", "")) >= gte[4:]]
                lim = q.get("limit", [None])[0]
                if lim:
                    rows = rows[: int(lim)]
                return rows
            if method == "POST":
                self.calls["log_insert"] += 1
                self.log_n += 1
                # 本番と同じく DB の一意制約で二重を拒否する（post_logs.op_id）
                op = (body or {}).get("op_id")
                if op and any(r.get("op_id") == op for r in self.post_logs):
                    # 本物のPostgRESTと同じ形の409を返す（コードを見ずに成功扱いしていないか確かめる）
                    dup = json.dumps({
                        "code": "23505",
                        "details": f"Key (op_id)=({op}) already exists.",
                        "message": 'duplicate key value violates unique constraint '
                                   '"post_logs_op_id_uniq"'}).encode()
                    raise urllib.error.HTTPError("u", 409, "Conflict", {}, io.BytesIO(dup))
                if self.log_fk_violation:
                    fk = json.dumps({"code": "23503",
                                     "message": "insert violates foreign key constraint"}).encode()
                    raise urllib.error.HTTPError("u", 409, "Conflict", {}, io.BytesIO(fk))
                beh = self.log_insert_behavior(self.log_n)
                if beh == "fail":
                    raise urllib.error.HTTPError("u", 500, "ISE", {}, io.BytesIO(b'{}'))
                if beh == "fail_but_saved":
                    self.post_logs.append(dict(body))
                    raise urllib.error.HTTPError("u", 500, "ISE", {}, io.BytesIO(b'{}'))
                self.post_logs.append(dict(body))
                return [body]
        if table == "post_attempts":
            if method == "GET":
                op = eqval("op_id")
                if op:
                    r = self.attempts.get(op)
                    return [dict(r)] if r else []
                rows = [dict(r) for r in self.attempts.values()]
                st = q.get("status", [None])[0]
                if st and st.startswith("in."):
                    want = set(st[4:-1].split(","))
                    rows = [r for r in rows if r.get("status") in want]
                jd = q.get("jst_date", [None])[0]
                if jd and jd.startswith("gte."):
                    rows = [r for r in rows if str(r.get("jst_date")) >= jd[4:]]
                # or=(status.in.(a,b,c),and(status.eq.running,jst_date.gte.X))
                orq = q.get("or", [None])[0]
                if orq:
                    m = re.search(r"status\.in\.\(([^)]*)\)", orq)
                    keep = set(m.group(1).split(",")) if m else set()
                    m2 = re.search(r"and\(status\.eq\.(\w+),jst_date\.gte\.([\d-]+)\)", orq)
                    m3 = re.search(r"and\(status\.eq\.(\w+),logged\.is\.(\w+)\)", orq)
                    m4 = re.findall(r"(?<!and\()status\.eq\.(\w+)", orq)
                    def _match(r):
                        if r.get("status") in keep:
                            return True
                        if m2 and r.get("status") == m2.group(1) \
                                and str(r.get("jst_date")) >= m2.group(2):
                            return True
                        if m3 and r.get("status") == m3.group(1) \
                                and bool(r.get("logged")) == (m3.group(2) == "true"):
                            return True
                        if m4 and r.get("status") in m4:
                            return True
                        return False
                    rows = [r for r in rows if _match(r)]
                sid_in = q.get("salon_id", [None])[0]
                if sid_in and sid_in.startswith("in."):
                    allowed = set(sid_in[4:-1].split(","))
                    rows = [r for r in rows if r.get("salon_id") in allowed]
                order = q.get("order", [None])[0]
                if order:
                    key = order.split(".")[0]
                    rows.sort(key=lambda r: str(r.get(key) or ""),
                              reverse=order.endswith(".desc"))
                off = q.get("offset", [None])[0]
                if off:
                    rows = rows[int(off):]
                lim = q.get("limit", [None])[0]
                if lim:
                    rows = rows[: int(lim)]
                return rows
            if method == "POST":
                op = body["op_id"]
                if op in self.attempts:
                    raise urllib.error.HTTPError("u", 409, "dup", {}, io.BytesIO(b'{}'))
                row = dict(body)
                row.setdefault("payload", None); row.setdefault("note", None)
                row["updated_at"] = self.db_now()
                self.attempts[op] = row
                return [dict(row)]
            if method == "PATCH":
                op = eqval("op_id"); rev = eqval("rev")
                if self.attempts_patch_behavior:
                    r = self.attempts_patch_behavior(op, body)
                    if r == "fail":
                        raise urllib.error.HTTPError("u", 500, "ISE", {}, io.BytesIO(b'{}'))
                cur = self.attempts.get(op)
                if cur is None or str(cur.get("rev")) != str(rev):
                    return []
                cur.update(body)
                # DBのトリガが now() を打つ（実行側が送った値は採用しない）
                cur["updated_at"] = self.db_now()
                return [dict(cur)]
        raise urllib.error.HTTPError("u", 404, "no table", {}, io.BytesIO(b'{}'))

W = World()

def install():
    real = urllib.request.urlopen
    def fake(req, timeout=None, **kw):
        if W.latency:
            if timeout is not None and W.latency > timeout:
                # 本物と同じく、指定タイムアウトを超えたら例外にする
                if W.on_latency:
                    W.on_latency(timeout)
                raise TimeoutError("timed out")
            if W.on_latency:
                W.on_latency(W.latency)
        url = req if isinstance(req, str) else req.full_url
        method = "GET" if isinstance(req, str) else req.get_method()
        parts = urllib.parse.urlsplit(url)
        qs = urllib.parse.parse_qs(parts.query)
        body = None
        if not isinstance(req, str) and req.data:
            raw = req.data.decode()
            try:
                body = json.loads(raw)
            except Exception:
                body = urllib.parse.parse_qs(raw)
        if "graph.threads.net" in parts.netloc:
            seg = parts.path.split("/")
            if seg[-1] == "threads" and method == "POST":
                out = W.th_create(body)
            elif seg[-1] == "threads_publish":
                out = W.th_publish(body)
            elif seg[-1] == "threads" and method == "GET":
                out = W.th_list(qs)
            elif seg[-1] == "me":
                W.calls["me"] = W.calls.get("me", 0) + 1
                beh = W.me_behavior(W.calls["me"]) if W.me_behavior else "ok"
                if beh == "401":
                    raise urllib.error.HTTPError("u", 401, "unauthorized", {},
                                                 io.BytesIO(b'{"error":{"message":"expired"}}'))
                if beh == "timeout":
                    raise TimeoutError("timed out")
                if beh == "empty":
                    out = {}
                else:
                    out = {"id": beh if beh not in ("ok",) else "USER1",
                           "username": "testsalon"}
            else:
                out = W.th_status(seg[-1])
            return Resp(json.dumps(out).encode())
        if "/rest/v1/" in parts.path:
            table = parts.path.split("/rest/v1/")[1]
            out = W.sb(method, table, parts.query, body)
            return Resp(json.dumps(out).encode())
        # ⚠️ 未知のURLは本物の通信に流さない。流すとテストが外部に依存し、
        # 「実は通信していた」ことに気づけない（2026-09-12 Sol指摘）
        raise AssertionError(f"テスト中に想定外の通信: {method} {url[:120]}")
    urllib.request.urlopen = fake
