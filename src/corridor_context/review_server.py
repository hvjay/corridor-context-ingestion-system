"""Local customer review and correction UI. Run with python -m corridor_context.review_server."""
from __future__ import annotations

import argparse
import json
import sqlite3
import secrets
import re
import tempfile
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .suggestions import suggest_resolution, accept_suggestion
from .corrections import create_proposal, apply_proposal, ignore_review, ConflictError, AgentUnavailable
from .attribution import assess_segments, resolve_target
from .db import (connect, init_db, transaction, get_active_fact_definitions, record_meeting,
                 start_ingestion_run, finish_ingestion_run)
from .env import load_local_env
from .reviews import backfill_reviews, customers, customer_reviews, record_reviews

ASSETS = Path(__file__).with_name("review_ui")


def prepare_database(path):
    conn = connect(path)
    try:
        init_db(conn)
        with transaction(conn):
            backfill_reviews(conn, get_active_fact_definitions(conn))
    finally:
        conn.close()


class ReviewHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, db_path, demo=False, **kwargs):
        self.db_path = db_path
        self.demo = demo
        super().__init__(*args, **kwargs)

    def send_content(self, status, data, content_type, nonce=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        style_policy = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
        self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self'; style-src {style_policy}; style-src-attr 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, status, value):
        self.send_content(status, json.dumps(value).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        # The application is local-only. Reject rebinding through arbitrary hosts.
        if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            self.json_response(403, {"error": "Local access only"})
            return
        path = unquote(urlsplit(self.path).path)
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if path in assets:
            name, kind = assets[path]
            data = (ASSETS / name).read_bytes()
            nonce = secrets.token_urlsafe(24) if name == "index.html" else None
            if nonce:
                data = data.replace(b"__CSP_NONCE__", nonce.encode())
            self.send_content(200, data, kind + "; charset=utf-8", nonce)
            return
        if path != "/api/customers" and not path.startswith("/api/customers/"):
            self.json_response(404, {"error": "Not found"})
            return
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            if path == "/api/customers":
                if not hasattr(self.server, "csrf_token"):
                    self.server.csrf_token = secrets.token_urlsafe(32)
                self.json_response(200, {"customers": customers(conn), "demo": self.demo, "csrfToken": self.server.csrf_token})
            else:
                self.json_response(200, customer_reviews(conn, path.removeprefix("/api/customers/")))
        except KeyError:
            self.json_response(404, {"error": "Customer not found"})
        except Exception:
            self.log_error("Unable to load review data")
            self.json_response(500, {"error": "Unable to load review data. Check the server log."})
        finally:
            conn.close()

    def do_POST(self):
        host = self.headers.get("Host")
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        token = getattr(self.server, "csrf_token", None)
        origin = self.headers.get("Origin")
        if host not in allowed_hosts or (origin and origin != "http://" + host) or not token or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), token):
            self.json_response(403, {"error": "Invalid request origin or session token. Refresh the page."})
            return
        match = re.fullmatch(r"/api/customers/([^/]+)/reviews/(\d+)/(proposals|apply|ignore|suggestion|accept-suggestion)", unquote(urlsplit(self.path).path))
        if not match:
            self.json_response(404, {"error": "Not found"})
            return
        conn = None
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Content-Type must be application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise ValueError("Request body must be between 1 and 32768 bytes")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request body must be an object")
            key, review_id, action = match.groups()
            review_id = int(review_id)
            conn = connect(self.db_path)
            if action == "accept-suggestion":
                result = accept_suggestion(conn, key, review_id, body.get("suggestionId"), body.get("reviewer"), body.get("requestId"))
            elif action == "suggestion":
                result = suggest_resolution(conn, key, review_id)
            elif action == "proposals":
                result = create_proposal(conn, key, review_id, body.get("instruction"), body.get("reviewer"), body.get("requestId"))
            elif action == "apply":
                result = apply_proposal(conn, key, review_id, body.get("proposalId"))
            else:
                result = ignore_review(conn, key, review_id, body.get("instruction"), body.get("reviewer"), body.get("requestId"))
            self.json_response(200, result)
        except KeyError:
            self.json_response(404, {"error": "Review or proposal not found for this customer"})
        except (ConflictError, sqlite3.IntegrityError) as exc:
            self.json_response(409, {"error": str(exc) if isinstance(exc, ConflictError) else "This request conflicts with an existing resolution. Refresh the page."})
        except AgentUnavailable as exc:
            self.json_response(503, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.json_response(400, {"error": str(exc)})
        except Exception:
            self.log_error("Correction request failed")
            self.json_response(500, {"error": "Unable to complete this correction. Refresh to check its status before retrying."})
        finally:
            if conn is not None:
                conn.close()


def demo_database():
    path = Path(tempfile.mkdtemp(prefix="corridor-review-demo-")) / "demo.sqlite3"
    prepare_database(path)
    conn = connect(path)
    try:
        definitions = get_active_fact_definitions(conn)
        run = start_ingestion_run(conn, "local-demo", None, "deterministic", None)
        fixture = Path(__file__).resolve().parents[2] / "fixtures" / "ambiguous_transcripts.json"
        meetings = json.loads(fixture.read_text())["meetings"]
        with transaction(conn):
            for meeting in meetings:
                record_meeting(conn, meeting, "demo", "processed")
                record_reviews(conn, run, meeting, resolve_target(meeting), assess_segments(meeting), definitions)
        finish_ingestion_run(conn, run, {"seen": len(meetings), "processed": len(meetings)})
    finally:
        conn.close()
    return path


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="Use a separate temporary database with labeled example data")
    args = parser.parse_args()
    if args.demo and args.db:
        parser.error("--demo uses a separate temporary database; do not combine with --db")
    path = demo_database() if args.demo else args.db or Path("data/client_context.sqlite3")
    prepare_database(path)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(ReviewHandler, db_path=path, demo=args.demo))
    print(f"Review UI: http://127.0.0.1:{server.server_port} | Database: {path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
