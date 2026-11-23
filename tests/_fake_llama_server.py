"""A stand-in for llama.cpp's llama-server, for tests/test_local_llm_server.py.

Takes the arguments the engine passes, writes a log as the real one does
(``-lv 4``: "offloaded N/M layers to GPU"), and answers /health and an
OpenAI-style streamed chat as the real server does: Server-Sent Events,
``text/event-stream`` with no charset, a last chunk with ``usage``.

What it says is decided by the last user message:
  "TOO LONG"   -> 400, the context-size error the real server gives
  "SLOW <n>"   -> n words, one every 50 ms (to be stopped part-way)
  anything else -> "Thanks — £8,400 is fine."
FAKE_LAYERS in the environment sets the log's layer count (default 43).
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def arg(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


PORT = int(arg("--port", "8080"))
LOG = arg("--log-file")
LAYERS = int(os.environ.get("FAKE_LAYERS", "43"))
if LOG:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\x1b[32mI \x1b[0mload_tensors: offloading 41 repeating layers to GPU\n")
        f.write(f"\x1b[32mI \x1b[0mload_tensors: offloaded {LAYERS}/43 layers to GPU\n")
        f.write(f"args: {json.dumps(sys.argv[1:])}\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        last = req["messages"][-1]["content"]
        if "TOO LONG" in last:
            body = json.dumps({"error": {"code": 400, "type": "exceed_context_size_error",
                                         "message": "the request exceeds the available context size"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if last.startswith("SLOW"):
            words, pause = [f" word{i}" for i in range(int(last.split()[1]))], 0.05
        else:
            words, pause = ["Thanks", " —", " £8,400", " is", " fine."], 0.0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")       # no charset, as the real one
        self.end_headers()
        try:
            for w in words:
                chunk = {"choices": [{"index": 0, "delta": {"content": w}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(pause)
            done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(done)}\n\n".encode())
            usage = {"choices": [], "usage": {"prompt_tokens": 1234, "completion_tokens": len(words)}}
            self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                    # the engine closed the connection: that is "stop"


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
