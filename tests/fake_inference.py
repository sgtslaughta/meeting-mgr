import json, threading
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

class FakeInference:
    def __init__(self):
        self.chat, self.transcriptions, self.errors = deque(), deque(), deque()
        self.requests = []
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def push_chat(self, obj): self.chat.append(obj)
    def push_transcription(self, obj): self.transcriptions.append(obj)
    def push_error(self, status): self.errors.append(status)
    def stop(self): self._server.shutdown()

    def _handler(self):
        outer = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                outer.requests.append((self.path, self.rfile.read(length)))
                if outer.errors:
                    self.send_response(outer.errors.popleft()); self.end_headers(); return
                if self.path.endswith("/chat/completions"):
                    payload = outer.chat.popleft() if outer.chat else {}
                    body = {"choices": [{"message": {"content": json.dumps(payload)}}]}
                elif self.path.endswith("/audio/transcriptions"):
                    body = outer.transcriptions.popleft() if outer.transcriptions else {"segments": []}
                else:
                    self.send_response(404); self.end_headers(); return
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        return H
