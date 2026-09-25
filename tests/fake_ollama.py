"""A stand-in Ollama server for offline tests. It speaks the same HTTP API
(/api/tags, /api/chat) and returns scripted answers, so the real client,
pipeline and CLI can be tested without a model. It is NOT a model: its
answers are hard-coded per test case."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

CASE_MARKERS = {
    "library managment": "poor",
    "HNSW": "tech",
    "Smart Attendance": "ai",
}

JUDGE = {
    "poor": dict(content_type="poorly_written", writing_quality="poor",
                 technical_depth="low", ai="low",
                 scores=dict(clarity=2, organisation=4, technical_depth=3,
                             specificity=3, authenticity=6),
                 quote="the main aim of project is to make the work of librarian easy",
                 details=["php and mysql", "books,students,issue"],
                 claims=["the project is working properly"]),
    "tech": dict(content_type="highly_technical", writing_quality="good",
                 technical_depth="high", ai="low",
                 scores=dict(clarity=7, organisation=8, technical_depth=9,
                             specificity=10, authenticity=9),
                 quote="M=16 with ef_construction=200 gave recall@10 of 0.962",
                 details=["recall@10 of 0.962", "p95 latency to 38 ms",
                          "1.8 GB to 462 MB"],
                 claims=[]),
    "ai": dict(content_type="likely_ai_generated", writing_quality="good",
               technical_depth="low", ai="high",
               scores=dict(clarity=8, organisation=7, technical_depth=2,
                           specificity=1, authenticity=2),
               quote="leverages the power of facial recognition technology",
               details=[],
               claims=["The proposed system offers a seamless and efficient "
                       "alternative to traditional methods."]),
}

# What llama3.1:8b actually returned on the first real run (22 Sep 2026):
# it rated the poorly written text as AI "high", and called the AI text
# "highly_technical" with AI likelihood "low" and generous scores.
REAL_FAILURES = {
    "poor": dict(JUDGE["poor"], ai="high",
                 scores=dict(clarity=4, organisation=5, technical_depth=3,
                             specificity=2, authenticity=7)),
    "ai": dict(JUDGE["ai"], content_type="highly_technical", ai="low",
               technical_depth="moderate",
               scores=dict(clarity=8, organisation=8, technical_depth=7,
                           specificity=6, authenticity=8),
               details=["facial recognition technology"]),
}


class State:
    def __init__(self):
        self.models = ["llama3.1:8b"]
        self.requests = []
        self.bad_json_once = False
        self.drift = {}          # case -> list of content_type overrides per judge call
        self.replay_real_failures = False
        self._calls = {}


def make_handler(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/tags":
                return self._send({"models": [{"name": m} for m in state.models]})
            self._send({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n))
            state.requests.append(payload)
            user = payload["messages"][1]["content"]
            case = next((v for k, v in CASE_MARKERS.items() if k in user), "tech")
            title = payload["format"].get("title", "")

            if state.bad_json_once:
                state.bad_json_once = False
                return self._send({"message": {"content": "Sure! Here is my answer:"}})

            if title in ("FinalSummary", "ChunkSummary"):
                content = {"summary": f"Scripted summary for the {case} case.",
                           "key_points": ["point one", "point two", "point three"]}
            else:
                j = (REAL_FAILURES.get(case, JUDGE[case])
                     if state.replay_real_failures else JUDGE[case])
                i = state._calls.get(case, 0)
                state._calls[case] = i + 1
                ctype = j["content_type"]
                ai = j["ai"]
                drift = state.drift.get(case)
                if drift and i < len(drift):
                    ctype, ai = drift[i]
                content = {
                    "concrete_details": j["details"],
                    "generic_claims": j["claims"],
                    "content_type": ctype,
                    "criteria": [{"criterion_id": cid, "score": sc,
                                  "justification": f"scripted reason for {cid}",
                                  "evidence_quote": j["quote"]}
                                 for cid, sc in j["scores"].items()],
                    "writing_quality": j["writing_quality"],
                    "technical_depth": j["technical_depth"],
                    "ai_style": {"likelihood": ai,
                                 "indicators": ["stock phrases", "no concrete numbers"]
                                 if ai != "low" else []},
                    "rationale": f"Scripted rationale for the {case} case.",
                }
            self._send({"message": {"content": json.dumps(content)}})
    return H


class FakeOllama:
    def __init__(self):
        self.state = State()
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
