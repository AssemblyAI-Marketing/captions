"""Offline tests for live/server.py: no API key or network. AssemblyAI is a fake. Run from the repo root:

    python3 -m unittest discover -s live -v
"""
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

API_KEY = "devkey0123456789abcdef0123456789"
USER_KEY = "userkey9876543210fedcba98765432"
SERVICE = "service-token-abc"
METADATA = {"issuer": server.ISSUER, "authorization_endpoint": "https://www.assemblyai.com/dashboard/oauth/authorize",
            "token_endpoint": "https://auth.assemblyai.com/v1/oauth2/token",
            "registration_endpoint": "https://auth.assemblyai.com/v1/oauth2/register"}


def jwt(exp):
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{part({'alg': 'none'})}.{part({'exp': exp, 'sub': 'member'})}.sig"


class FakeAssemblyAI:
    """Answers the URLs server.py calls, and records each call."""

    def __init__(self, now):
        self.now = now
        self.calls = []
        self.access = jwt(now() + 3600)
        self.exchange = (200, {"api_key": USER_KEY, "connection_id": "conn_1"})

    def __call__(self, url, method="GET", headers=None, data=None):
        self.calls.append((method, url, dict(headers or {}), data))
        path = urllib.parse.urlsplit(url).path
        if path == "/.well-known/oauth-authorization-server":
            return 200, METADATA
        if path == "/v1/oauth2/register":
            return 201, {"client_id": "client_123"}
        if path == "/v1/oauth2/token":
            return 200, {"access_token": self.access, "refresh_token": "refresh_1", "expires_in": 3600}
        if path == "/api/v2/oauth/api-key":
            return self.exchange
        if path == "/v3/token":
            ok = (headers or {}).get("authorization") in (API_KEY, USER_KEY)
            return (200, {"token": "stream-token", "expires_in_seconds": 60}) if ok else (401, {"error": "Invalid API key"})
        raise AssertionError(f"unexpected call {url}")

    def paths(self):
        return [urllib.parse.urlsplit(c[1]).path for c in self.calls]


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def make(mode="oauth", **kw):
    clock = Clock()
    fake = FakeAssemblyAI(clock)
    app = server.App(mode=mode, api_key=API_KEY if mode == "dev" else None, secret="s" * 32, fetch=fake, now=clock,
                     service_token=SERVICE if mode == "oauth" else None, **kw)
    return app, fake, clock


def call(app, method, target, cookie="", host="localhost:8787", origin=None):
    headers = {"host": host, "cookie": cookie}
    if origin:
        headers["origin"] = origin
    with contextlib.redirect_stderr(io.StringIO()):
        return app.handle(method, target, headers)


def set_cookies(response):
    return {v.split("=", 1)[0]: v.split("=", 1)[1].split(";", 1)[0] for k, v in response.headers if k == "Set-Cookie"}


def sign_in(app):
    """Run the whole flow against the fakes; returns the session cookie header."""
    r = call(app, "GET", "/login")
    login = set_cookies(r)[server.LOGIN_COOKIE]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(dict(r.headers)["Location"]).query))["state"]
    r = call(app, "GET", f"/callback?code=abc&state={state}&iss={urllib.parse.quote(server.ISSUER)}",
             cookie=f"{server.LOGIN_COOKIE}={login}")
    assert r.status == 303, r.body
    return f"{server.SESSION_COOKIE}={set_cookies(r)[server.SESSION_COOKIE]}"


class Pkce(unittest.TestCase):
    def test_rfc7636_example(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"   # RFC 7636, appendix B
        self.assertEqual(server.b64url(hashlib.sha256(verifier.encode()).digest()),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_pair(self):
        verifier, challenge = server.pkce_pair()
        self.assertRegex(verifier, r"^[A-Za-z0-9\-._~]{43,128}$")
        self.assertEqual(challenge, server.b64url(hashlib.sha256(verifier.encode()).digest()))
        self.assertNotEqual(verifier, server.pkce_pair()[0])

    def test_authorize_redirect(self):
        app, fake, _ = make()
        r = call(app, "GET", "/login")
        self.assertEqual(r.status, 303)
        url = urllib.parse.urlsplit(dict(r.headers)["Location"])
        q = dict(urllib.parse.parse_qsl(url.query))
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", METADATA["authorization_endpoint"])
        self.assertEqual((q["response_type"], q["client_id"], q["code_challenge_method"], q["scope"]),
                         ("code", "client_123", "S256", "aai:api offline_access"))
        self.assertEqual(q["redirect_uri"], "http://localhost:8787/callback")
        self.assertGreaterEqual(len(q["state"]), 32)
        cookie = [v for k, v in r.headers if k == "Set-Cookie"][0]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn(q["code_challenge"], cookie)   # the cookie holds the verifier (signed), not the challenge
        # Registration happened once, as a public client (no secret), with our redirect URI.
        body = json.loads(fake.calls[1][3])
        self.assertEqual((body["token_endpoint_auth_method"], body["redirect_uris"]),
                         ("none", ["http://localhost:8787/callback"]))
        call(app, "GET", "/login")
        self.assertEqual(fake.paths().count("/v1/oauth2/register"), 1)

    def test_login_moves_to_the_public_host_first(self):
        app, _, _ = make()
        r = call(app, "GET", "/login", host="127.0.0.1:8787")
        self.assertEqual(dict(r.headers)["Location"], "http://localhost:8787/login")


class Callback(unittest.TestCase):
    def setUp(self):
        self.app, self.fake, self.clock = make()
        r = call(self.app, "GET", "/login")
        self.login = set_cookies(r)[server.LOGIN_COOKIE]
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(dict(r.headers)["Location"]).query))
        self.state, self.challenge = q["state"], q["code_challenge"]

    def callback(self, query, login=None):
        login = self.login if login is None else login
        return call(self.app, "GET", f"/callback?{urllib.parse.urlencode(query)}", cookie=f"{server.LOGIN_COOKIE}={login}")

    def assertRefused(self, r):
        self.assertEqual(r.status, 400)
        self.assertNotIn(server.SESSION_COOKIE, set_cookies(r))
        self.assertNotIn("/v1/oauth2/token", self.fake.paths())

    def test_good_state_signs_in(self):
        r = self.callback({"code": "abc", "state": self.state})
        self.assertEqual((r.status, dict(r.headers)["Location"]), (303, "/"))
        self.assertIn(server.SESSION_COOKIE, set_cookies(r))
        token_call = [c for c in self.fake.calls if c[1].endswith("/v1/oauth2/token")][0]
        form = dict(urllib.parse.parse_qsl(token_call[3].decode()))
        self.assertEqual((form["grant_type"], form["code"]), ("authorization_code", "abc"))
        self.assertEqual(server.b64url(hashlib.sha256(form["code_verifier"].encode()).digest()), self.challenge)
        cookie = [v for k, v in r.headers if k == "Set-Cookie" and v.startswith(server.SESSION_COOKIE + "=")][0]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_wrong_state(self):
        self.assertRefused(self.callback({"code": "abc", "state": "not-the-state"}))

    def test_missing_state(self):
        self.assertRefused(self.callback({"code": "abc"}))

    def test_no_login_cookie(self):
        r = call(self.app, "GET", f"/callback?code=abc&state={self.state}")
        self.assertRefused(r)

    def test_tampered_login_cookie(self):
        value, mac = self.login.rsplit(".", 1)
        forged = json.loads(server.unb64url(value))
        forged["state"] = "attacker"
        forged = server.b64url(json.dumps(forged).encode())
        self.assertRefused(self.callback({"code": "abc", "state": "attacker"}, login=f"{forged}.{mac}"))

    def test_expired_login(self):
        self.clock.t += server.LOGIN_SECONDS + 1
        self.assertRefused(self.callback({"code": "abc", "state": self.state}))

    def test_other_issuer(self):
        self.assertRefused(self.callback({"code": "abc", "state": self.state, "iss": "https://evil.example"}))

    def test_denied(self):
        r = self.callback({"error": "access_denied", "state": self.state})
        self.assertEqual(dict(r.headers)["Location"], "/?error=denied")
        self.assertNotIn(server.SESSION_COOKIE, set_cookies(r))


class TokenRoute(unittest.TestCase):
    def test_needs_a_session(self):
        app, fake, _ = make()
        self.assertEqual(call(app, "POST", "/api/token").status, 401)
        self.assertEqual(call(app, "POST", "/api/token", cookie=f"{server.SESSION_COOKIE}=forged.sig").status, 401)
        self.assertEqual(fake.calls, [])

    def test_signed_in_gets_only_a_streaming_token(self):
        app, fake, _ = make()
        cookie = sign_in(app)
        r = call(app, "POST", "/api/token", cookie=cookie, origin="http://localhost:8787")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.body), {"token": "stream-token", "expires_in_seconds": 60})
        exchange = [c for c in fake.calls if c[1].endswith("/api/v2/oauth/api-key")][0]
        self.assertEqual(exchange[2]["x-aai-service-token"], SERVICE)
        self.assertEqual(exchange[2]["authorization"], f"Bearer {fake.access}")
        streaming = [c for c in fake.calls if c[1].startswith(server.STREAMING_TOKEN_URL)][-1]
        self.assertEqual(streaming[2]["authorization"], USER_KEY)
        self.assertIn("expires_in_seconds=60", streaming[1])

    def test_cross_origin_post_refused(self):
        app, _, _ = make()
        cookie = sign_in(app)
        self.assertEqual(call(app, "POST", "/api/token", cookie=cookie, origin="https://evil.example").status, 403)

    def test_key_cached_then_reexchanged(self):
        app, fake, clock = make()
        cookie = sign_in(app)
        call(app, "POST", "/api/token", cookie=cookie)
        call(app, "POST", "/api/token", cookie=cookie)
        self.assertEqual(fake.paths().count("/api/v2/oauth/api-key"), 1)
        clock.t += server.MAX_CACHE_SECONDS + 1
        call(app, "POST", "/api/token", cookie=cookie)
        self.assertEqual(fake.paths().count("/api/v2/oauth/api-key"), 2)

    def test_cache_stops_at_rotation(self):
        app, fake, clock = make()
        fake.exchange = (200, {"api_key": USER_KEY, "api_key_rotates_at": "2027-01-15T08:00:10Z"})
        clock.t = server._timestamp("2027-01-15T08:00:00Z")
        fake.access = jwt(clock.t + 3600)
        cookie = sign_in(app)
        call(app, "POST", "/api/token", cookie=cookie)
        clock.t += 11
        call(app, "POST", "/api/token", cookie=cookie)
        self.assertEqual(fake.paths().count("/api/v2/oauth/api-key"), 2)

    def test_refused_service_token_is_an_outage(self):
        app, fake, _ = make()
        cookie = sign_in(app)
        fake.exchange = (401, {"detail": "Unknown client"})
        r = call(app, "POST", "/api/token", cookie=cookie)
        self.assertEqual(r.status, 503)
        self.assertNotIn(server.SESSION_COOKIE, set_cookies(r))   # signing in again can't fix it: the session stays
        self.assertIn("approved", json.loads(r.body)["detail"])

    def test_refused_token_signs_out(self):
        app, fake, _ = make()
        cookie = sign_in(app)
        fake.exchange = (401, {"detail": "Token revoked"})
        r = call(app, "POST", "/api/token", cookie=cookie)
        self.assertEqual(r.status, 401)
        self.assertEqual(set_cookies(r)[server.SESSION_COOKIE], "")   # cleared

    def test_missing_scope(self):
        app, fake, _ = make()
        cookie = sign_in(app)
        fake.exchange = (403, {"detail": "Missing scope aai:api"})
        self.assertEqual(call(app, "POST", "/api/token", cookie=cookie).status, 403)

    def test_no_service_token(self):
        app, _, _ = make(mode="oauth")
        app.service_token = None
        cookie = sign_in(app)
        r = call(app, "POST", "/api/token", cookie=cookie)
        self.assertEqual(r.status, 503)
        self.assertIn("service token", json.loads(r.body)["detail"])

    def test_expired_access_token_is_refreshed(self):
        app, fake, clock = make()
        cookie = sign_in(app)
        clock.t += 3600
        fake.access = jwt(clock.t + 3600)
        self.assertEqual(call(app, "POST", "/api/token", cookie=cookie).status, 200)
        refresh = [c for c in fake.calls if c[1].endswith("/v1/oauth2/token")][-1]
        self.assertEqual(dict(urllib.parse.parse_qsl(refresh[3].decode()))["grant_type"], "refresh_token")

    def test_logout(self):
        """Logout clears the session cookie. Sessions live in the cookie and the issuer has no revocation
        endpoint, so a copy of the old cookie works until it expires (see SECURITY.md)."""
        app, _, _ = make()
        cookie = sign_in(app)
        r = call(app, "POST", "/logout", cookie=cookie)
        self.assertEqual(set_cookies(r)[server.SESSION_COOKIE], "")
        self.assertIn("Max-Age=0", [v for k, v in r.headers if k == "Set-Cookie" and v.startswith(server.SESSION_COOKIE)][0])
        self.assertEqual(call(app, "POST", "/api/token", cookie=f"{server.SESSION_COOKIE}=").status, 401)


class Modes(unittest.TestCase):
    def test_dev_mode_uses_the_server_key(self):
        app, fake, _ = make(mode="dev")
        r = call(app, "POST", "/api/token")
        self.assertEqual(r.status, 200)
        self.assertEqual(fake.calls[0][2]["authorization"], API_KEY)
        self.assertEqual(fake.paths(), ["/v3/token"])   # no OAuth anywhere
        self.assertEqual(json.loads(call(app, "GET", "/api/config").body),
                         {"mode": "dev", "signed_in": True, "can_sign_in": False})
        self.assertEqual(dict(call(app, "GET", "/login").headers)["Location"], "/")

    def test_dev_mode_only_serves_this_machine(self):
        app, fake, _ = make(mode="dev")
        for host in ("evil.example:8787", "192.168.1.5:8787"):   # DNS rebinding, or a LAN visitor
            self.assertEqual(call(app, "POST", "/api/token", host=host).status, 403)
        self.assertEqual(call(app, "POST", "/api/token", host="127.0.0.1:8787").status, 200)
        app.on_vercel = True
        self.assertEqual(call(app, "POST", "/api/token").status, 403)

    def test_dev_mode_needs_a_key(self):
        with self.assertRaises(SystemExit):
            server.App(mode="dev", api_key=None)
        with self.assertRaises(SystemExit):
            server.App(mode="nope", api_key=API_KEY)

    def test_oauth_config(self):
        app, _, _ = make()
        self.assertEqual(json.loads(call(app, "GET", "/api/config").body),
                         {"mode": "oauth", "signed_in": False, "can_sign_in": True})
        cookie = sign_in(app)
        self.assertTrue(json.loads(call(app, "GET", "/api/config", cookie=cookie).body)["signed_in"])

    def test_from_env(self):
        self.assertEqual(server.App.from_env({"LIVE_MODE": "oauth"}).mode, "oauth")
        app = server.App.from_env({"LIVE_MODE": "dev", "ASSEMBLYAI_API_KEY": API_KEY, "LIVE_PORT": "9000"})
        self.assertEqual((app.mode, app.api_key, app.public_url), ("dev", API_KEY, "http://localhost:9000"))

    def test_page_csp_allows_only_its_own_script(self):
        app, _, _ = make(mode="dev")
        r = call(app, "GET", "/")
        csp = dict(r.headers)["Content-Security-Policy"]
        script = re.search(rb"<script>(.*?)</script>", r.body, re.S).group(1)
        self.assertIn(f"'sha256-{base64.b64encode(hashlib.sha256(script).digest()).decode()}'", csp)
        self.assertNotIn("'unsafe-inline' blob", csp)
        self.assertIn("wss://streaming.assemblyai.com", csp)
        self.assertIn(b"Live captions on", r.body)
        self.assertIn(b"powered by AssemblyAI", r.body)


class NoSecretsOut(unittest.TestCase):
    """Over a real socket: the key, access token, refresh token and code never reach a response or the log."""

    def run_server(self, app):
        handler = type("H", (server.Handler,), {"app": app})
        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join()))
        app.public_url = f"http://localhost:{httpd.server_port}"
        return httpd.server_port

    def get(self, port, path, method="GET", cookie=""):
        req = urllib.request.Request(f"http://localhost:{port}{path}", method=method, headers={"cookie": cookie})
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, str(r.headers), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, str(e.headers), e.read()

    def test_flow(self):
        for mode in ("dev", "oauth"):
            with self.subTest(mode=mode):
                app, fake, _ = make(mode=mode)
                port = self.run_server(app)
                log = io.StringIO()
                seen = []
                with contextlib.redirect_stderr(log):
                    if mode == "oauth":
                        status, headers, _ = self.get(port, "/login")
                        login = re.search(rf"{server.LOGIN_COOKIE}=([^;]+)", headers).group(1)
                        state = re.search(r"state=([^&\s]+)", headers).group(1)
                        status, headers, body = self.get(port, f"/callback?code=secret-code&state={state}",
                                                         cookie=f"{server.LOGIN_COOKIE}={login}")
                        seen.append(headers + body.decode())
                        cookie = re.search(rf"({server.SESSION_COOKIE}=[^;]+)", headers).group(1)
                    else:
                        cookie = ""
                    for path, method in (("/", "GET"), ("/api/config", "GET"), ("/api/token", "POST")):
                        status, headers, body = self.get(port, path, method, cookie)
                        seen.append(headers + body.decode())
                    self.assertEqual(status, 200)
                    self.assertIn("stream-token", seen[-1])
                for secret in (API_KEY, USER_KEY, fake.access, "refresh_1", "secret-code"):
                    for text in seen + [log.getvalue()]:
                        self.assertNotIn(secret, text)
                self.assertIn("/api/token", log.getvalue())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


if __name__ == "__main__":
    unittest.main()


class OutboundAllowlist(unittest.TestCase):
    """fetch() only calls AssemblyAI's configured hosts (SSRF hardening)."""

    def test_assemblyai_hosts_allowed(self):
        for url in (f"{server.ISSUER}/.well-known/oauth-authorization-server", f"{server.EXCHANGE_ORIGIN}/api/v2/oauth/api-key",
                    f"{server.STREAMING_TOKEN_URL}?expires_in_seconds=60"):
            self.assertEqual(server.allowed_url(url), url)

    def test_other_hosts_refused(self):
        for url in ("https://169.254.169.254/latest/meta-data/", "https://evil.example/token", "http://www.assemblyai.com/x",
                    "https://user:pw@www.assemblyai.com/x", "file:///etc/passwd", "https://auth.assemblyai.com.evil.example/"):
            with self.assertRaises(ValueError, msg=url):
                server.allowed_url(url)

    def test_fetch_refuses_before_any_network_call(self):
        with self.assertRaises(server.RefusedURL):
            server.fetch("https://169.254.169.254/latest/meta-data/")

    def test_refused_url_takes_the_unreachable_path(self):
        """Callers catch OSError for "AssemblyAI could not be reached": a refusal must land there, not crash."""
        self.assertTrue(issubclass(server.RefusedURL, OSError))

        def refuse(url, **kw):
            raise server.RefusedURL(url)

        app = server.App(mode="dev", api_key=API_KEY, secret="s" * 32, fetch=refuse)
        r = call(app, "POST", "/api/token", host="127.0.0.1:8787")
        self.assertEqual(r.status, 503)


class VercelEntry(unittest.TestCase):
    def test_no_module_level_app(self):
        """Vercel's Python runtime takes a module-level "app" for a WSGI/ASGI callable: the entry must not define one."""
        import ast
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "api", "index.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = {t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)}
        self.assertNotIn("app", names)
        self.assertIn("handler", {n.name for n in tree.body if isinstance(n, ast.ClassDef)})


class StatelessSessions(unittest.TestCase):
    """Sessions live in an encrypted cookie: on Vercel, requests land on different instances."""

    def test_session_works_on_another_instance(self):
        app, fake, clock = make()
        cookie = sign_in(app)
        other = server.App(mode="oauth", secret="s" * 32, fetch=fake, now=clock, service_token=SERVICE)
        r = call(other, "POST", "/api/token", cookie=cookie)
        self.assertEqual(r.status, 200, r.body)
        self.assertTrue(json.loads(call(other, "GET", "/api/config", cookie=cookie).body)["signed_in"])

    def test_other_secret_or_tampering_refused(self):
        app, fake, clock = make()
        cookie = sign_in(app)
        stranger = server.App(mode="oauth", secret="t" * 32, fetch=fake, now=clock, service_token=SERVICE)
        self.assertEqual(call(stranger, "POST", "/api/token", cookie=cookie).status, 401)
        name, value = cookie.split("=", 1)
        flipped = value[:-6] + ("A" if value[-6] != "A" else "B") + value[-5:]
        self.assertEqual(call(app, "POST", "/api/token", cookie=f"{name}={flipped}").status, 401)

    def test_tokens_not_readable_in_the_cookie(self):
        app, fake, _ = make()
        cookie = sign_in(app)
        raw = server.unb64url(cookie.split("=", 1)[1][3:])
        for secret in (fake.access, "refresh_1"):
            self.assertNotIn(secret.encode(), raw)
            self.assertNotIn(secret, cookie)

    def test_expired_session_signs_in_again(self):
        app, _, clock = make()
        cookie = sign_in(app)
        clock.t += server.SESSION_SECONDS + 1
        self.assertEqual(call(app, "POST", "/api/token", cookie=cookie).status, 401)

    def test_refresh_rewrites_the_cookie(self):
        app, fake, clock = make()
        cookie = sign_in(app)
        clock.t += 3600                                  # the access token expired; the session hasn't
        fake.access = jwt(clock.t + 3600)
        r = call(app, "POST", "/api/token", cookie=cookie)
        self.assertEqual(r.status, 200, r.body)
        self.assertIn("refresh_token", [c[3] and urllib.parse.parse_qs(c[3].decode()).get("grant_type", [""])[0]
                                        for c in fake.calls if c[1].endswith("/v1/oauth2/token")][-1])
        renewed = f"{server.SESSION_COOKIE}={set_cookies(r)[server.SESSION_COOKIE]}"
        self.assertNotEqual(renewed, cookie)
        self.assertEqual(app.session({"cookie": renewed})["access_token"], fake.access)

    def test_large_token_keeps_cookie_small(self):
        app, fake, _ = make()
        fake.access = jwt(fake.now() + 3600) + "x" * 5000   # bigger than a cookie can hold
        cookie = sign_in(app)
        self.assertLess(len(cookie), 4000)
        self.assertNotIn("access_token", app.session({"cookie": cookie}))
        self.assertEqual(call(app, "POST", "/api/token", cookie=cookie).status, 200)   # refreshes on demand

