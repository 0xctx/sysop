"""sysop session serving: appended to common.py in a private Kaggle notebook. Ends by itself.

llama-server runs on 127.0.0.1 behind a small proxy that refuses every request without the
session key (except /health), streams responses, and tracks use. ngrok exposes the proxy on the
operator's static domain, with the token read from the operator's private dataset. The session ends on
`POST /sysop/down`, after `idle_minutes` without use, or at `max_hours`, never later.
"""
import hmac
import http.client
import http.server
import subprocess
import sys
import threading

TEST_PORT = os.environ.get("SYSOP_TEST_SERVE")  # offline tests: no tunnel, a fake upstream, this port
PROXY = int(TEST_PORT or JOB.get("port", 8080))
UPSTREAM = PROXY + 1  # llama-server, never exposed
KEY = JOB.get("key", "")
stop = threading.Event()
state = {"ready": False, "last_used": time.time(), "requests": 0, "exit": None}
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length",
       "proxy-authorization", "proxy-authenticate"}


def status() -> dict:
    now = time.time()
    return {"model": JOB.get("id"), "ready": state["ready"], "requests": state["requests"],
            "idle_s": round(now - state["last_used"]), "left_s": round(T0 + JOB.get("max_hours", 4) * 3600 - now)}


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # prompts never reach the notebook log
        pass

    def reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def route(self) -> None:
        if self.path == "/health":  # open, like llama-server's: says loading or ok, nothing else
            return self.reply(200 if state["ready"] else 503, {"status": "ok" if state["ready"] else "loading"})
        if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {KEY}"):
            return self.reply(401, {"error": "a session key is required"})
        if self.path == "/sysop/status":
            return self.reply(200, status())
        if self.path == "/sysop/down" and self.command == "POST":
            state["exit"] = "down"
            stop.set()
            return self.reply(200, {"stopping": True})
        if not state["ready"]:
            return self.reply(503, {"error": "the model is still loading"})
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self.reply(411, {"error": "send Content-Length; chunked request bodies are not accepted"})
        state["requests"] += 1
        state["last_used"] = time.time()
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        upstream = http.client.HTTPConnection("127.0.0.1", UPSTREAM, timeout=900)
        upstream.request(self.command, self.path, body=body or None,
                         headers={k: v for k, v in self.headers.items() if k.lower() not in HOP})
        res = upstream.getresponse()
        self.send_response(res.status)
        for k, v in res.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")  # streams (SSE) pass through as they come
        self.end_headers()
        while chunk := res.read1(65536):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
            state["last_used"] = time.time()  # a long generation is use, not idleness
        self.wfile.write(b"0\r\n\r\n")
        upstream.close()

    do_GET = do_POST = do_PUT = do_DELETE = route


def fake_upstream() -> None:
    """Tests and the CPU spike: a tiny OpenAI-shaped echo in place of llama-server."""
    class Echo(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            self.answer({"data": [{"id": JOB.get("id", "fake")}]})

        def do_POST(self) -> None:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            text = "echo: " + str(req.get("messages", [{}])[-1].get("content", ""))[:40]
            self.answer({"choices": [{"message": {"role": "assistant", "content": text}}],
                         "usage": {"completion_tokens": 2}})

        def answer(self, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", UPSTREAM), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def tunnel() -> str | None:
    """ngrok on the operator's static domain. The token comes from the operator's private dataset
    (Kaggle Secrets do not reach CLI-pushed notebooks) and is never printed."""
    if not JOB.get("domain") or TEST_PORT:
        return None  # offline tests: the proxy is the endpoint
    [path] = glob.glob(f"{KAGGLE}/input/**/ngrok.token", recursive=True) or [None]
    if not path:
        raise RuntimeError("no ngrok token attached: run `sysop.py serve token` on your machine")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ngrok"], check=True)
    import ngrok
    return ngrok.forward(PROXY, authtoken=open(path).read().strip(), domain=JOB["domain"]).url()


def main() -> None:
    if not KEY:
        raise RuntimeError("no session key: an unkeyed endpoint is never opened")
    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", PROXY), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    metrics["url"] = tunnel()  # up first, so `serve status` can say "loading" while the model loads
    mark("tunnel_s")
    if JOB.get("fake_upstream") or TEST_PORT:
        fake_upstream()
        mark("ready_s")
    else:
        start_model(UPSTREAM, ["--api-key", KEY, "--jinja"])
    state["ready"], state["last_used"] = True, time.time()
    idle, cap = JOB.get("idle_minutes", 20) * 60, T0 + JOB.get("max_hours", 4) * 3600
    while not stop.wait(2):
        if time.time() - state["last_used"] > idle:
            state["exit"] = "idle"
            break
        if time.time() > cap:
            state["exit"] = "cap"
            break
    time.sleep(1)  # let the down reply leave before the tunnel closes
    metrics.update(exit=state["exit"], requests=state["requests"])
    mark("ended_s")
    evidence()  # GPU memory and the server log tail at the end, as on errors


try:
    main()
except Exception as exc:  # noqa: BLE001 - the metrics say what failed
    metrics["error"] = str(exc)[-2000:]
    evidence()
    raise
finally:
    stop_server()
