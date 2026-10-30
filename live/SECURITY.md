# Security: live captions

Report vulnerabilities as in the repo's [SECURITY.md](../SECURITY.md): GitHub's private vulnerability reporting, not a public issue.

## Where things go

- **API keys.** Dev mode reads `ASSEMBLYAI_API_KEY` from the environment or the git-ignored `.env`. Oauth mode gets each visitor's key from AssemblyAI's key exchange, and keeps it in memory for five minutes at most. Keys are only sent to `streaming.assemblyai.com`, to mint streaming tokens. They're never sent to the browser, written to disk or logged.
- **What the browser gets.** One streaming token per session, which must be used within 60 seconds (`expires_in_seconds=60`). Until then it can open sessions billed to the key that minted it. That's why it's short-lived, and why dev mode only answers `localhost`.
- **OAuth tokens.** Access and refresh tokens stay in server memory, under a random session ID. The browser holds only that ID, in a signed, `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS). The PKCE verifier and `state` sit in a signed, `HttpOnly`, 10-minute cookie, and are checked on the callback, as is the `iss` (RFC 9207). Sign out drops the session.
- **The service token** (oauth mode) goes only to AssemblyAI's key exchange, in `X-AAI-Service-Token`. On Vercel it's the project's OIDC token, read fresh on each request.
- **Audio.** It goes from the browser straight to AssemblyAI over `wss://` and never passes through this server. Nothing is recorded or saved here. What AssemblyAI keeps depends on the account: see the consent section of [README.md](README.md).
- **Logs.** Request paths without query strings (the callback's query carries the code and state), plus JSON events: hashed token IDs, connection IDs, HTTP statuses. Never a key, token or code. `test_live.py` checks this over a real socket.
- **The page.** A Content-Security-Policy allows only the page's own inline script (by hash) and the AudioWorklet blob. It can connect only to this server and AssemblyAI's streaming hosts, and it can't be framed. The page also sends no referrer and stores nothing (settings live in the URL). POSTs from another origin are refused. Dev mode checks the `Host` header, which also stops DNS rebinding.
- **Dependencies.** None: the Python standard library, and a static page with no external scripts, styles or fonts.
