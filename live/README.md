# Live captions

Live captions of your own microphone, with AssemblyAI realtime Speech-to-Text (Universal-3.5 Pro Realtime by default, or Universal-3.6 Pro Realtime). Partial words show dimmed and turn solid when they're final, and only the last one or two lines stay on screen. It fits anywhere:

- **OBS / Streamlabs:** a transparent overlay on your stream.
- **Zoom, Meet, Teams:** an always-on-top caption window you share, or captions on your camera through OBS's virtual camera.
- **A stage screen:** full screen, big type.

The rest of this repo captions finished videos. This folder is the live version: one page (`index.html`) and one small server (`server.py`), Python standard library only, nothing to install.

## Run it

```bash
python3 live/server.py
```

Open http://localhost:8787, click **Start captions**, allow the microphone, and talk.

The page has settings for language, model, font, size, position, lines, style and background. They're kept in the page's URL, so a bookmark or an overlay URL carries them. The page saves nothing.

- **Language.** *Any* lets the model follow code-switching across every language it knows. A single language or a pair (Hindi + English, Spanish + English, …) steers it toward those (`language_codes`). Korean, Russian and the other languages only Universal-3.6 Pro knows switch the model to 3.6 Pro, and the page says so.
- **Font.** System fonts (sans, rounded, serif, monospace), or the name of any font installed on the computer. No font files ship with this.

## Two modes

The server runs in one of two modes, and the page shows which one is on.

| | `dev` (default) | `oauth` |
|---|---|---|
| Who pays | You: your `ASSEMBLYAI_API_KEY` | Each visitor: their own AssemblyAI account |
| Who can use it | Only this computer (localhost) | Anyone you give the URL to |
| Needs | An API key in the repo's `.env` | AssemblyAI's one-time approval of the deployment (below) |

**Dev mode** reads `ASSEMBLYAI_API_KEY` from the environment or the repo's `.env` (as the rest of this repo does). It refuses requests that don't come from `localhost`, so it can't spend your key for someone else, even through DNS rebinding. Use it for demos on your own machine.

**OAuth mode** puts a **Sign in with AssemblyAI** button on the page:

```bash
LIVE_MODE=oauth python3 live/server.py
```

### What the browser gets

In both modes the browser gets just one credential: a streaming token from `GET https://streaming.assemblyai.com/v3/token?expires_in_seconds=60`, which must be used within a minute. The page opens `wss://streaming.assemblyai.com/v3/ws` with it. The API key, OAuth access token and refresh token stay in the server's memory. The server's log shows request paths without query strings (the sign-in callback carries a code and state in its query).

## Sign in with AssemblyAI

This copies how the [AssemblyAI MCP server](https://github.com/AssemblyAI/assemblyai-mcp) signs people in (`src/oauth.ts`):

1. **Discovery.** The server reads `https://auth.assemblyai.com/.well-known/oauth-authorization-server`.
2. **Registration.** It registers itself as an OAuth client by dynamic client registration (RFC 7591), as a public client with redirect URI `<LIVE_PUBLIC_URL>/callback`. `python3 live/server.py --register` does this once and prints an `ASSEMBLYAI_OAUTH_CLIENT_ID` to set, so a server doesn't register again every time it starts.
3. **Sign-in.** **Sign in with AssemblyAI** sends the visitor to AssemblyAI's sign-in and consent page:
   - scope `aai:api offline_access`;
   - PKCE (S256);
   - a random `state`, kept with the PKCE verifier in a signed, httpOnly, 10-minute cookie.
4. **Callback.** `/callback` refuses a missing, wrong or expired `state`, and an `iss` that isn't AssemblyAI's (RFC 9207). Then it trades the code for tokens. Those stay in server memory, under a random session ID. The session ID goes to the browser in a signed, httpOnly, `SameSite=Strict` cookie (`Secure` over HTTPS).
5. **Key exchange.** AssemblyAI's API takes API keys, not access tokens. So for each streaming token, the server swaps the access token for the visitor's key at `POST https://www.assemblyai.com/api/v2/oauth/api-key`:
   - the access token goes as the Bearer, and a **service token** goes in `X-AAI-Service-Token`;
   - as in the MCP, keys are cached for five minutes at most, and never past the key's rotation or the token's expiry, so a revoked connection is noticed within minutes;
   - a refused token signs the visitor out (403 for a missing scope);
   - a refused service token (`401 Unknown client`) or an outage is a 503, because signing in again wouldn't fix it.

   Access tokens are refreshed with the refresh token when they expire.

**The catch is the service token.** AssemblyAI only accepts it from deployments it has approved. The MCP server proves itself with its **Vercel OIDC token**: AssemblyAI trusts that one Vercel project's production deployment. A copy of the MCP can't exchange tokens, and neither can this server until AssemblyAI approves it. Until then, sign-in reaches the consent page and comes back, but **Start captions** says AssemblyAI hasn't approved this server. Dev mode works today.

### Deploy on Vercel (the easiest way to get a service token)

Vercel gives every function request the project's OIDC token. So deploying this folder to Vercel gets a service token the same way the MCP does, with no secret to manage. `api/index.py` and `vercel.json` route every path to `server.py`, as a Python function.

1. Create a Vercel project with **Root Directory** `live`. Leave **Secure Backend Access with OIDC Federation** on (the default).
2. Set these environment variables:
   - `LIVE_MODE=oauth`;
   - `LIVE_PUBLIC_URL=https://<your domain>`;
   - `LIVE_SESSION_SECRET=` a long random string (`python3 -c "import secrets; print(secrets.token_hex(32))"`);
   - `ASSEMBLYAI_OAUTH_CLIENT_ID=` from `LIVE_PUBLIC_URL=https://<your domain> python3 live/server.py --register`.
3. Deploy, and send AssemblyAI the approval request below.

Sign-ins live in the function's memory. A request that lands on a fresh instance asks the visitor to sign in again, which is one click because AssemblyAI remembers the consent. That's fine for a demo. For heavier use, keep sessions in a shared store such as Redis.

Elsewhere, set `ASSEMBLYAI_SERVICE_TOKEN` to whatever token AssemblyAI agrees to accept.

### The approval request

Send this to AssemblyAI's platform team (the people who set up the MCP server's token exchange):

> **Subject: Approve a deployment to exchange OAuth access tokens for API keys (live captions demo)**
>
> We'd like "Sign in with AssemblyAI" on a public live-captions demo: `<LIVE_PUBLIC_URL>`, code at `https://github.com/AssemblyAI-Marketing/captions/tree/main/live`. It works like the AssemblyAI MCP server: it signs members in at `https://auth.assemblyai.com` (dynamic client registration, PKCE, scope `aai:api offline_access`), then exchanges the access token at `POST /api/v2/oauth/api-key` for the member's key and uses it server-side only, to mint 60-second streaming tokens (`GET /v3/token`). The key and access token never reach the browser or the logs; keys are cached at most five minutes, as in the MCP's `src/oauth.ts`.
>
> Please accept this deployment's service token in `X-AAI-Service-Token`, the same way you accept the MCP server's Vercel OIDC token:
>
> - Vercel team: `<team slug>`, project: `<project name>`, project ID: `<prj_…>`
> - OIDC issuer: `https://oidc.vercel.com/<team slug>`, audience: `https://vercel.com/<team slug>`, environment: `production`
> - OAuth client ID: `<ASSEMBLYAI_OAUTH_CLIENT_ID>`, redirect URI: `<LIVE_PUBLIC_URL>/callback`
> - Contact: `<name, email>`
>
> Until it's approved, the demo runs in dev mode on a single key.

Get the issuer and audience from the project's settings, or decode a token's claims (`iss`, `aud`, `project_id`, `environment`). The MCP logs these same claims when AssemblyAI refuses its service token.

## Overlays

**Copy overlay URL** gives the same settings with `overlay=1`: just the captions and the label, on a transparent background, and it starts on load. Options: `bg=transparent|green|dark`, `pos=bottom|middle|top`, `lines=1|2`, `size=44`, `font=sans|rounded|serif|mono|custom&fontname=…`, `style=box|outline`, `lang=en` or `lang=hi,en` (or `lang=custom&codes=…`), `model=universal-3-6-pro`.

- **OBS / Streamlabs, Browser Source.** Add a Browser Source with the overlay URL (with `bg=transparent`), sized like your canvas. A browser source can't reach the mic unless OBS starts with `--enable-media-stream` (on macOS, `open -a OBS --args --enable-media-stream`). In oauth mode, right-click the source, choose **Interact**, and sign in once.
- **OBS / Streamlabs, Window Capture.** Open the overlay URL with `bg=green` in its own browser window, capture the window, and add a Chroma Key filter. This works without launch flags.
- **Zoom, Meet, Teams, as a window.** **Pop out, always on top** opens the captions in a small window that stays above everything (Chrome and Edge, through Document Picture-in-Picture). Share that window, or keep it beside the call.
- **Zoom, Meet, Teams, on your camera.** Put the overlay on your camera in OBS, start **Virtual Camera**, and pick "OBS Virtual Camera" as your camera in the call.
- **Stage screen.** **Full screen** on the display computer, with a large size and black background.

## Consent

Captioning sends speech to AssemblyAI live: audio leaves the computer the whole time captions are on, even though this page saves nothing. So:

- **Tell people captions are on.** The page always shows "Live captions on · powered by AssemblyAI" while it's listening; keep it visible.
- **Get consent where it's required.** Some places and settings (some US states, the EU, workplaces, classrooms, healthcare) require everyone's consent before their voice is processed by a service like this, whether or not it's recorded.
- **Know what AssemblyAI keeps.** AssemblyAI's [data retention page](https://www.assemblyai.com/docs/data-retention-and-model-training) says it offers zero data retention of streaming audio and transcripts for accounts **opted out of model training**, and keeps some metadata for logging and billing. If your account isn't opted out, don't promise people that nothing is kept. Streaming data is processed in the US or EU ([endpoints and data zones](https://www.assemblyai.com/docs/streaming/endpoints-and-data-zones)).

This is plain-language guidance, not legal advice. Where keys, tokens and audio go: [SECURITY.md](SECURITY.md).

## Settings

| Variable | Default | |
|---|---|---|
| `LIVE_MODE` | `dev` | `dev` or `oauth` |
| `ASSEMBLYAI_API_KEY` | from `.env` | dev mode's key |
| `LIVE_HOST`, `LIVE_PORT` | `127.0.0.1`, `8787` | where to listen |
| `LIVE_PUBLIC_URL` | `http://localhost:<port>` | oauth mode: this server's address (the redirect URI is `<it>/callback`) |
| `LIVE_SESSION_SECRET` | random each start | signs cookies; set it for anything but a quick local try |
| `ASSEMBLYAI_OAUTH_CLIENT_ID` | registered on first sign-in | from `--register` |
| `ASSEMBLYAI_SERVICE_TOKEN` | none (on Vercel, its OIDC token) | proves this server to the key exchange |
| `ASSEMBLYAI_OAUTH_ISSUER`, `ASSEMBLYAI_OAUTH_EXCHANGE_ORIGIN` | AssemblyAI production | other AssemblyAI environments, as in the MCP |

## Tests

```bash
python3 -m unittest discover -s live -v
```

These run offline against a fake AssemblyAI. They cover:

- PKCE (including RFC 7636's example);
- `state` and `iss` checks on the callback;
- the token route needing a session;
- the key exchange's caching and error handling;
- token refresh;
- dev mode staying local;
- the page's Content-Security-Policy;
- a real-socket run of both modes, showing that the key, access token, refresh token and code never appear in a response or the log.

## Files

```
live/
├── index.html      the page: sign-in, mic → 16 kHz PCM (AudioWorklet), WebSocket, captions, settings
├── server.py       serves the page, OAuth, key exchange, streaming tokens
├── test_live.py    offline tests
├── SECURITY.md     where keys, tokens and audio go
├── api/index.py    Vercel entry point (oauth mode)
└── vercel.json     routes every path to api/index.py
```
