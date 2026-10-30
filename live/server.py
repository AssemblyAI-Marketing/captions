"""Live captions of your mic with AssemblyAI realtime Speech-to-Text.

    python3 live/server.py                    dev mode: your ASSEMBLYAI_API_KEY, http://localhost:8787
    LIVE_MODE=oauth python3 live/server.py    "Sign in with AssemblyAI": each visitor uses their own account
    python3 live/server.py --register         oauth mode: register this server once, print its client ID

Serves index.html and hands the browser a streaming token that must be used within a minute. The API
key and OAuth tokens stay in this process: they never reach the browser or the log. Standard library
only. See live/README.md.

Environment:
    LIVE_MODE                    dev (default) or oauth
    ASSEMBLYAI_API_KEY           dev mode: your key (or ASSEMBLYAI_API_KEY=... in the repo's .env)
    LIVE_HOST, LIVE_PORT         where to listen (default 127.0.0.1:8787)
    LIVE_PUBLIC_URL              oauth mode: this server's address, for the redirect URI (default http://localhost:PORT)
    LIVE_SESSION_SECRET          oauth mode: signs cookies; set it when the server restarts or runs on more than one instance
    ASSEMBLYAI_OAUTH_CLIENT_ID   oauth mode: a client registered with --register (else one is registered on first sign-in)
    ASSEMBLYAI_SERVICE_TOKEN     oauth mode: proves this server to AssemblyAI's key exchange (on Vercel, its OIDC token)
    ASSEMBLYAI_OAUTH_ISSUER, ASSEMBLYAI_OAUTH_EXCHANGE_ORIGIN   other AssemblyAI environments, as in the MCP server
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ISSUER = os.environ.get("ASSEMBLYAI_OAUTH_ISSUER", "https://auth.assemblyai.com").rstrip("/")
EXCHANGE_ORIGIN = os.environ.get("ASSEMBLYAI_OAUTH_EXCHANGE_ORIGIN", "https://www.assemblyai.com").rstrip("/")
SCOPES = "aai:api offline_access"   # the API itself, and a refresh token so nobody is sent back to consent every few hours
STREAMING_TOKEN_URL = "https://streaming.assemblyai.com/v3/token"
STREAMING_TOKEN_SECONDS = 60        # the browser opens its WebSocket right away; the token can't be reused after this
STREAMING_HOSTS = "wss://streaming.assemblyai.com wss://streaming.us.assemblyai.com wss://streaming.eu.assemblyai.com"

SESSION_COOKIE = "aai_live"
LOGIN_COOKIE = "aai_live_login"
SESSION_SECONDS = 12 * 3600
LOGIN_SECONDS = 600
MAX_SESSIONS = 1000
MAX_CACHE_SECONDS = 300             # re-exchange at least this often, to notice a revoked connection (as the MCP does)
MAX_CACHE_ENTRIES = 1000
TIMEOUT = 10


# ---------- small helpers ----------

def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pkce_pair():
    """(verifier, S256 challenge), RFC 7636."""
    verifier = b64url(secrets.token_bytes(32))   # 43 characters, all unreserved
    return verifier, b64url(hashlib.sha256(verifier.encode()).digest())


def jwt_exp(token):
    """A JWT's exp in seconds, read without verifying it: it only bounds how long we cache. AssemblyAI verifies."""
    try:
        exp = json.loads(unb64url(token.split(".")[1])).get("exp")
        return exp if isinstance(exp, (int, float)) else None
    except (IndexError, ValueError, AttributeError):
        return None


def api_key_from_env(env=os.environ):
    key = env.get("ASSEMBLYAI_API_KEY")
    for path in (os.path.join(ROOT, ".env"), ".env"):
        if not key and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("ASSEMBLYAI_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("\"'")
    return key or None


def fetch(url, method="GET", headers=None, data=None):
    """(status, JSON body or None). Raises OSError when AssemblyAI can't be reached."""
    req = urllib.request.Request(url, data=data, headers={"accept": "application/json", **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, _json(r.read())
    except urllib.error.HTTPError as e:
        with e:
            return e.code, _json(e.read())


def _json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def detail_of(body, status):
    body = body if isinstance(body, dict) else {}
    text = body.get("detail") or body.get("error_description") or body.get("error")
    return text if isinstance(text, str) else f"HTTP {status}"


class Response:
    def __init__(self, status, body=b"", content_type="application/json", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.status, self.body = status, body
        self.headers = [("Content-Type", content_type), ("Cache-Control", "no-store"), *headers]


def redirect(location, *headers):
    return Response(303, headers=[("Location", location), *headers])


# ---------- the app: everything but the socket, so tests and Vercel can call it ----------

class App:
    def __init__(self, mode="dev", api_key=None, public_url="http://localhost:8787", secret=None, client_id=None,
                 service_token=None, fetch=fetch, now=time.time, on_vercel=False):
        if mode not in ("dev", "oauth"):
            raise SystemExit(f"LIVE_MODE must be dev or oauth, not {mode!r}")
        if mode == "dev" and not api_key:
            raise SystemExit("Dev mode needs ASSEMBLYAI_API_KEY (or ASSEMBLYAI_API_KEY=... in the repo's .env), "
                             "get one at https://www.assemblyai.com/dashboard. Or LIVE_MODE=oauth.")
        self.mode, self.api_key, self.public_url = mode, api_key, public_url.rstrip("/")
        self.secret = (secret or secrets.token_hex(32)).encode()
        self.client = {"client_id": client_id} if client_id else None
        self.service_token, self.fetch, self.now, self.on_vercel = service_token, fetch, now, on_vercel
        self.secure = self.public_url.startswith("https://")
        self.metadata = None
        self.sessions = {}      # session id -> {"access_token", "refresh_token", "expires_at", "until"}
        self.keys = {}          # sha256(access token) -> (api key, cache until)
        self.lock = threading.Lock()

    @classmethod
    def from_env(cls, env=os.environ, **kw):
        mode = env.get("LIVE_MODE", "dev").strip().lower()
        port = env.get("LIVE_PORT", "8787")
        return cls(mode=mode, api_key=api_key_from_env(env) if mode == "dev" else None,
                   public_url=env.get("LIVE_PUBLIC_URL") or f"http://localhost:{port}",
                   secret=env.get("LIVE_SESSION_SECRET"), client_id=env.get("ASSEMBLYAI_OAUTH_CLIENT_ID"),
                   service_token=env.get("ASSEMBLYAI_SERVICE_TOKEN"),
                   on_vercel=bool(env.get("VERCEL")), **kw)

    # ----- routing -----

    def handle(self, method, target, headers):
        """headers: lowercase name -> value. Returns a Response."""
        url = urllib.parse.urlsplit(target)
        query = dict(urllib.parse.parse_qsl(url.query))
        routes = {
            ("GET", "/"): self.page, ("GET", "/index.html"): self.page,
            ("GET", "/api/config"): self.config,
            ("POST", "/api/token"): self.streaming_token,
            ("GET", "/login"): self.login,
            ("GET", "/callback"): self.callback,
            ("POST", "/logout"): self.logout,
        }
        route = routes.get((method, url.path))
        if not route:
            return Response(404, {"error": "not_found"})
        if method == "POST" and not self.same_origin(headers):
            return Response(403, {"error": "cross_origin"})
        return route(query, headers)

    def same_origin(self, headers):
        """POSTs come from our own page. Browsers send Origin on every POST fetch; no Origin means not a browser."""
        origin = headers.get("origin")
        return origin is None or origin == self.public_url or origin == f"http://{headers.get('host', '')}"

    def page(self, query, headers):
        with open(os.path.join(HERE, "index.html"), "rb") as f:
            html = f.read()
        # Hash the page's own inline script, so nothing else inline can run.
        scripts = re.findall(rb"<script>(.*?)</script>", html, re.S)
        hashes = " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(s).digest()).decode()}'" for s in scripts)
        csp = (f"default-src 'self'; script-src {hashes} blob:; style-src 'self' 'unsafe-inline'; "
               f"connect-src 'self' {STREAMING_HOSTS}; img-src 'self' data:; base-uri 'none'; form-action 'self'; "
               f"frame-ancestors 'none'")
        return Response(200, html, "text/html; charset=utf-8", [
            ("Content-Security-Policy", csp), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"), ("Permissions-Policy", "microphone=(self), camera=()")])

    def config(self, query, headers):
        return Response(200, {"mode": self.mode, "signed_in": self.mode == "dev" or self.session(headers) is not None,
                              "can_sign_in": self.mode == "oauth"})

    # ----- the streaming token: the only credential the browser ever sees -----

    def streaming_token(self, query, headers):
        if self.mode == "dev":
            # Dev mode spends your key, so it only answers pages opened on this machine. The Host check also
            # stops DNS rebinding (a web page that points its own hostname at 127.0.0.1).
            host = headers.get("host", "").rsplit(":", 1)[0]
            if host not in ("localhost", "127.0.0.1", "[::1]") or self.on_vercel:
                return Response(403, {"error": "dev_mode_is_local", "detail": "Dev mode only serves this machine. "
                                      "Use LIVE_MODE=oauth to let others sign in with their own account."})
            key = self.api_key
        else:
            sid, session = self.session_entry(headers)
            if not session:
                return Response(401, {"error": "sign_in"})
            access = self.fresh_access_token(sid, session)
            if not access:
                return Response(401, {"error": "sign_in"}, headers=[("Set-Cookie", self.clear(SESSION_COOKIE))])
            kind, value = self.exchange(access, headers)
            if kind == "rejected":
                self.drop(sid)
                return Response(401, {"error": "sign_in", "detail": value},
                                headers=[("Set-Cookie", self.clear(SESSION_COOKIE))])
            if kind == "insufficient_scope":
                return Response(403, {"error": "insufficient_scope", "detail": value})
            if kind == "unavailable":
                return Response(503, {"error": "temporarily_unavailable", "detail": value}, headers=[("Retry-After", "5")])
            key = value
        try:
            status, body = self.fetch(f"{STREAMING_TOKEN_URL}?expires_in_seconds={STREAMING_TOKEN_SECONDS}",
                                      headers={"authorization": key})
        except OSError:
            return Response(503, {"error": "temporarily_unavailable", "detail": "AssemblyAI could not be reached"})
        token = body.get("token") if isinstance(body, dict) else None
        if status != 200 or not isinstance(token, str):
            detail = detail_of(body, status).replace(key, "[key]")
            hint = " Check ASSEMBLYAI_API_KEY." if self.mode == "dev" and 400 <= status < 500 and status != 429 else ""
            return Response(502, {"error": "no_streaming_token", "detail": f"AssemblyAI: {detail}.{hint}"})
        return Response(200, {"token": token, "expires_in_seconds": STREAMING_TOKEN_SECONDS})

    def exchange(self, access_token, headers):
        """("key", api key) | ("rejected" | "insufficient_scope" | "unavailable", detail). Like src/oauth.ts in
        AssemblyAI/assemblyai-mcp: keys cached per access token for at most five minutes, never past the key's
        rotation or the token's expiry; a refused service token is an outage, not a reason to sign in again."""
        entry = hashlib.sha256(access_token.encode()).hexdigest()
        now = self.now()
        with self.lock:
            cached = self.keys.get(entry)
            if cached and cached[1] > now:
                return "key", cached[0]
            self.keys.pop(entry, None)
        # On Vercel the project's OIDC token comes with each request (and in the env, rotated), read fresh as
        # @vercel/oidc does; elsewhere it's ASSEMBLYAI_SERVICE_TOKEN.
        service_token = ((headers.get("x-vercel-oidc-token") or os.environ.get("VERCEL_OIDC_TOKEN"))
                         if self.on_vercel else None) or self.service_token
        if not service_token:
            log("oauth_exchange_unavailable", reason="no_service_token")
            return "unavailable", ("This server has no AssemblyAI service token yet, so it can't use your sign-in. "
                                   "See live/README.md, Sign in with AssemblyAI.")
        try:
            status, body = self.fetch(f"{EXCHANGE_ORIGIN}/api/v2/oauth/api-key", method="POST", headers={
                "authorization": f"Bearer {access_token}", "x-aai-service-token": service_token})
        except OSError as e:
            log("oauth_exchange_unavailable", reason="network", detail=type(e).__name__)
            return "unavailable", "AssemblyAI could not be reached"
        body = body if isinstance(body, dict) else {}
        if 200 <= status < 300:
            key = body.get("api_key")
            if not isinstance(key, str) or not key:
                log("oauth_exchange_unavailable", reason="no_key_in_response")
                return "unavailable", "AssemblyAI returned no API key"
            until = min(now + MAX_CACHE_SECONDS, jwt_exp(access_token) or float("inf"),
                        _timestamp(body.get("api_key_rotates_at")) or float("inf"))
            with self.lock:
                if len(self.keys) >= MAX_CACHE_ENTRIES:
                    self.keys.pop(next(iter(self.keys)))
                self.keys[entry] = (key, until)
            log("oauth_exchange_ok", connection_id=body.get("connection_id"), token_hash=entry[:12])
            return "key", key
        detail = detail_of(body, status)
        if status == 401 and detail == "Unknown client":
            log("oauth_exchange_unavailable", reason="service_token_refused", status=401)
            return "unavailable", ("AssemblyAI hasn't approved this server to use sign-ins yet (Unknown client). "
                                   "See live/README.md, Sign in with AssemblyAI.")
        if status in (401, 403):
            log("oauth_exchange_rejected", status=status, token_hash=entry[:12], detail=detail)
            scope = status == 403 and re.search("scope", detail, re.I)
            return ("insufficient_scope" if scope else "rejected"), detail
        log("oauth_exchange_unavailable", reason="upstream", status=status, detail=detail)
        return "unavailable", "AssemblyAI could not issue an API key; try again"

    # ----- OAuth: dynamic registration, PKCE, state, callback, refresh -----

    def oauth_metadata(self):
        if not self.metadata:
            status, body = self.fetch(f"{ISSUER}/.well-known/oauth-authorization-server")
            if status != 200 or not isinstance(body, dict) or body.get("issuer") != ISSUER:
                raise OSError(f"no authorization server metadata at {ISSUER} (HTTP {status})")
            self.metadata = body
        return self.metadata

    def oauth_client(self):
        """This server's OAuth client: from ASSEMBLYAI_OAUTH_CLIENT_ID, or registered once (RFC 7591)."""
        if not self.client:
            status, body = self.fetch(self.oauth_metadata()["registration_endpoint"], method="POST",
                                      headers={"content-type": "application/json"}, data=json.dumps({
                                          "client_name": "Live captions (AssemblyAI)",
                                          "redirect_uris": [self.redirect_uri],
                                          "grant_types": ["authorization_code", "refresh_token"],
                                          "response_types": ["code"],
                                          "token_endpoint_auth_method": "none",
                                          "scope": SCOPES}).encode())
            if status not in (200, 201) or not isinstance(body, dict) or not body.get("client_id"):
                raise OSError(f"client registration failed: {detail_of(body, status)}")
            self.client = {k: body[k] for k in ("client_id", "client_secret") if body.get(k)}
            log("oauth_client_registered", client_id=self.client["client_id"])
        return self.client

    @property
    def redirect_uri(self):
        return f"{self.public_url}/callback"

    def login(self, query, headers):
        if self.mode != "oauth":
            return redirect("/")
        if urllib.parse.urlsplit(self.public_url).netloc != headers.get("host"):
            return redirect(f"{self.public_url}/login")   # cookies are per host: start where the callback lands
        try:
            authorize, client = self.oauth_metadata()["authorization_endpoint"], self.oauth_client()
        except OSError as e:
            log("oauth_login_unavailable", detail=str(e))
            return redirect("/?error=unavailable")
        state = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        params = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": self.redirect_uri,
                  "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        login = self.sign(b64url(json.dumps({"state": state, "verifier": verifier,
                                             "until": self.now() + LOGIN_SECONDS}).encode()))
        # Lax, not Strict: it has to come back with AssemblyAI's redirect to /callback.
        return redirect(f"{authorize}?{urllib.parse.urlencode(params)}",
                        ("Set-Cookie", self.cookie(LOGIN_COOKIE, login, LOGIN_SECONDS, "Lax")))

    def callback(self, query, headers):
        clear_login = ("Set-Cookie", self.clear(LOGIN_COOKIE))
        if self.mode != "oauth":
            return redirect("/")
        login = self.unsign(self.cookies(headers).get(LOGIN_COOKIE))
        try:
            login = json.loads(unb64url(login)) if login else None
        except ValueError:
            login = None
        if not login or login.get("until", 0) < self.now() or not hmac.compare_digest(
                str(login.get("state")), query.get("state", "")):
            log("oauth_callback_rejected", reason="state")
            return Response(400, {"error": "invalid_state", "detail": "Sign-in expired or didn't start here. "
                                  "Go back and click Sign in again."}, headers=[clear_login])
        if "iss" in query and query["iss"] != ISSUER:   # RFC 9207: the code came from the issuer we sent them to
            return Response(400, {"error": "invalid_issuer"}, headers=[clear_login])
        if query.get("error"):
            return redirect("/?error=denied" if query["error"] == "access_denied" else "/?error=signin", clear_login)
        if not query.get("code"):
            return Response(400, {"error": "missing_code"}, headers=[clear_login])
        tokens = self.token_request({"grant_type": "authorization_code", "code": query["code"],
                                     "redirect_uri": self.redirect_uri, "code_verifier": login["verifier"]})
        if not tokens:
            return redirect("/?error=signin", clear_login)
        sid = secrets.token_urlsafe(32)
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[sid] = {**tokens, "until": self.now() + SESSION_SECONDS}
        return redirect("/", clear_login, ("Set-Cookie", self.cookie(SESSION_COOKIE, self.sign(sid), SESSION_SECONDS)))

    def token_request(self, form):
        """Tokens from the token endpoint, or None. Keeps only what we need, server-side."""
        client = self.oauth_client()
        form = {**form, **client}
        try:
            status, body = self.fetch(self.oauth_metadata()["token_endpoint"], method="POST",
                                      headers={"content-type": "application/x-www-form-urlencoded"},
                                      data=urllib.parse.urlencode(form).encode())
        except OSError as e:
            log("oauth_token_unavailable", detail=type(e).__name__)
            return None
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            log("oauth_token_rejected", status=status, grant=form["grant_type"], detail=detail_of(body, status))
            return None
        access = body["access_token"]
        expires = self.now() + body["expires_in"] if isinstance(body.get("expires_in"), (int, float)) else jwt_exp(access)
        return {"access_token": access, "refresh_token": body.get("refresh_token") or form.get("refresh_token"),
                "expires_at": expires}

    def fresh_access_token(self, sid, session):
        """The session's access token, refreshed when it expires within a minute. None: sign in again."""
        if session.get("expires_at") and session["expires_at"] - 60 < self.now():
            refreshed = session.get("refresh_token") and self.token_request(
                {"grant_type": "refresh_token", "refresh_token": session["refresh_token"]})
            if not refreshed:
                self.drop(sid)
                return None
            with self.lock:
                session.update(refreshed)
        return session["access_token"]

    def logout(self, query, headers):
        sid, _ = self.session_entry(headers)
        self.drop(sid)
        return redirect("/", ("Set-Cookie", self.clear(SESSION_COOKIE)))

    # ----- sessions and cookies -----

    def session(self, headers):
        return self.session_entry(headers)[1]

    def session_entry(self, headers):
        sid = self.unsign(self.cookies(headers).get(SESSION_COOKIE))
        with self.lock:
            session = self.sessions.get(sid) if sid else None
            if session and session["until"] < self.now():
                self.sessions.pop(sid, None)
                session = None
        return sid, session

    def drop(self, sid):
        with self.lock:
            self.sessions.pop(sid, None)

    def sign(self, value):
        return f"{value}.{b64url(hmac.new(self.secret, value.encode(), hashlib.sha256).digest())}"

    def unsign(self, signed):
        value, _, mac = (signed or "").rpartition(".")
        good = b64url(hmac.new(self.secret, value.encode(), hashlib.sha256).digest())
        return value if value and hmac.compare_digest(mac, good) else None

    def cookie(self, name, value, max_age, same_site="Strict"):
        secure = "; Secure" if self.secure else ""
        return f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite={same_site}{secure}"

    def clear(self, name):
        return self.cookie(name, "", 0, "Lax")

    @staticmethod
    def cookies(headers):
        jar = {}
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            jar.setdefault(name, value)
        return jar


def _timestamp(text):
    """Seconds since the epoch for an ISO 8601 time, or None."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def log(event, **fields):
    """One JSON line per event on stderr. Never pass a key, token or code here."""
    sys.stderr.write(json.dumps({"event": event, **{k: v for k, v in fields.items() if v is not None}}) + "\n")


# ---------- the HTTP server ----------

class Handler(http.server.BaseHTTPRequestHandler):
    app = None
    server_version = "live-captions"
    sys_version = ""

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.rfile.read(min(int(self.headers.get("content-length") or 0), 65536))
        self.respond()

    def respond(self):
        headers = {k.lower(): v for k, v in self.headers.items()}
        r = self.app.handle(self.command, self.path, headers)
        self.send_response(r.status)
        for name, value in r.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(r.body)))
        self.end_headers()
        self.wfile.write(r.body)

    def log_message(self, format, *args):
        # Paths only: /callback carries the authorization code and state in its query.
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), re.sub(r"\?\S*", "?…", format % args)))


def main(argv=sys.argv[1:]):
    if argv[:1] == ["--register"]:
        app = App.from_env({**os.environ, "LIVE_MODE": "oauth"})
        print(f"redirect URI {app.redirect_uri}\nASSEMBLYAI_OAUTH_CLIENT_ID={app.oauth_client()['client_id']}")
        return
    app = App.from_env()
    host, port = os.environ.get("LIVE_HOST", "127.0.0.1"), int(os.environ.get("LIVE_PORT", "8787"))
    if app.mode == "oauth" and not os.environ.get("LIVE_SESSION_SECRET"):
        print("No LIVE_SESSION_SECRET: sign-ins last until this server stops.", file=sys.stderr)
    Handler.app = app
    server = http.server.ThreadingHTTPServer((host, port), Handler)
    print(f"Live captions ({app.mode} mode) on {app.public_url if app.mode == 'oauth' else f'http://localhost:{port}'}"
          "  · Ctrl+C to stop", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
