#!/usr/bin/env bash
# v1 e2e, the runtime verifier with a real verifier agent (haiku, curl only): calibrate on a deliberate
# fault, then a reviewed branch with a planted regression is refused and the fixed branch lands.
# Driven by the CLI alone (no coordinating session), so it is cheap: about six short verifier runs.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/verify"
rm -rf "$REPO" && mkdir -p "$REPO" && cd "$REPO" && git init -q -b main
git config user.name e2e && git config user.email e2e@example.com
S="python3 $SYSOP/scripts/sysop.py"
cat > app.py <<'PY'
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
TODOS = []
class H(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health": return self.reply(200, {"ok": True})
        if self.path == "/todos": return self.reply(200, TODOS)
        self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path == "/todos":
            title = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}").get("title")
            todo = {"id": len(TODOS) + 1, "title": title, "done": False}
            TODOS.append(todo); return self.reply(201, todo)
        if self.path.startswith("/todos/") and self.path.endswith("/done"):
            for t in TODOS:
                if str(t["id"]) == self.path.split("/")[2]:
                    t["done"] = True; return self.reply(200, t)
        self.reply(404, {"error": "not found"})
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
PY
cat > JOURNEYS.md <<'MD'
1. **Finish a todo.** A client creates "ship v1" with `POST /todos` (JSON `{"title": "ship v1"}`), marks it done with `POST /todos/<id>/done`, and `GET /todos` then shows "ship v1" with `"done": true`. Value: finished work shows as finished.
MD
claude_only .claude/sysop.json
python3 - .claude/sysop.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d.update(check="python3 -c 'import app' 2>/dev/null || true", start="python3 app.py")
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY
git add -A && git commit -qm "todo app with a journey"
plant() {  # plant <branch> <python replacement for the done line>
  git checkout -qb "$1" main
  sed -i "s|t\[\"done\"\] = True; return self.reply(200, t)|$2|" app.py
  git commit -qam "$1"; git checkout -q main
}
plant fault 'return self.reply(200, {**t, "done": True})'
expect "calibration: the base passes and the fault fails" $S verify calibrate --fault-branch fault --base main
seal() { mkdir -p .sysop/reports; printf '**Verdict:** READY\n' > ".sysop/reports/$1.review.md"; $S review seal "$1" >/dev/null; }
plant regress 'return self.reply(200, {**t, "done": True})  # refactor'
seal regress
$S verify run regress --author-family openai > "$E2E_ROOT/regress.json" 2>&1
expect "the planted regression fails verification" grep -q '"verdict": "FAIL"' "$E2E_ROOT/regress.json"
expect "and it cannot land" bash -c "! $S land regress --base main --land"
git checkout -qb fixed main && echo "# a harmless change" >> app.py && git commit -qam "fixed" && git checkout -q main
seal fixed
$S verify run fixed --author-family openai > "$E2E_ROOT/fixed.json" 2>&1
expect "the good branch passes verification" grep -q '"verdict": "PASS"' "$E2E_ROOT/fixed.json"
expect "and lands" bash -c "$S land fixed --base main --land | grep -q '\"landed\": true'"
expect "no app left running" bash -c "! pgrep -f '[p]ython3 app.py' >/dev/null"
finish v1_verify
