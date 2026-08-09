# Deploying the PaxCast API

Until an API is reachable over HTTPS, both clients install and load but show
"Could not reach the forecast service", and the contribute screens fail with
"Could not reach the server. Nothing was saved." Nothing in the app can be
fixed to work around this: adding an airport is a write, and a write needs a
server.

This takes about five minutes and needs a free Render account.

---

## 1. Deploy

1. Sign up at <https://render.com> (free; GitHub login works).
2. **New → Blueprint**.
3. Select the `paxcast` repository and apply.

Render reads [`render.yaml`](render.yaml) and provisions the service — there is
nothing to fill in by hand. The first build takes 3–5 minutes, most of it
compiling nothing and downloading numpy/scipy wheels.

**Copy the URL from the service page in the Render dashboard.** Do not construct
it from the service name: names are globally unique across all of Render, so if
`paxcast-api` is already taken your service gets a suffixed hostname such as
`paxcast-api-a7x2.onrender.com`.

Confirm it is alive before going further, substituting your own host:

```bash
curl https://YOUR-SERVICE.onrender.com/health
```

Expected: `{"status":"ok","airports":20,...}`. The `airports` count proves the
catalogue seeded; a zero there means the database did not initialise.

### If you get `Not Found`

Check which layer answered:

```bash
curl -i https://YOUR-SERVICE.onrender.com/health
```

- `x-render-routing: no-server` with `server: cloudflare` — **no service is
  attached to that hostname.** Either the blueprint was never applied, or you
  are using the wrong URL. Go back to the dashboard and copy the real one.
- `server: uvicorn` with a JSON body — the app is running and you simply hit a
  path it does not serve. `/health` and `/` both exist; check the spelling.

A deploy that built but crashed on boot shows as a failed deploy in the
dashboard with the traceback in its log, not as `Not Found`.

---

## 2. Point the clients at it

Add the URL as a repository **variable** (not a secret — it is inlined into the
client bundle and is public the moment anyone opens the app):

**Settings → Secrets and variables → Actions → Variables → New variable**

| Name | Value |
|---|---|
| `API_BASE_URL` | `https://paxcast-api.onrender.com` |

Then rebuild both clients:

```bash
gh workflow run "Web (GitHub Pages)"   # website, ~3 min
gh workflow run "Android APK"          # APK artifact, ~20 min
git tag v0.1.1 && git push --tags      # publishes a new APK release
```

Both workflows warn loudly in the log when `API_BASE_URL` is unset, so a build
that silently ships the wrong endpoint is not a failure mode here.

---

## 3. Two properties of the free plan you should know

**It sleeps.** Free services spin down after ~15 minutes idle. The next request
wakes them, taking 30–60 seconds. The app's first screen will sit on its
loading state for that long, then work normally. If someone is evaluating the
product, warn them or upgrade the plan.

**Contributed data is not durable.** SQLite lives on the instance filesystem,
which the free plan wipes on redeploy and spin-down. The 20-airport catalogue
re-seeds automatically on every boot, so the app never comes back empty — but
airports you add, schedules you import and waits you report are gone. Attach a
persistent disk (paid) and set `PAXCAST_DB` to a path on its mount to keep
them.

That second point matters more than it looks: the checkpoint calibration story
depends on observations accumulating over time. On an ephemeral disk the fit
never leaves its prior, because the reports keep vanishing.

---

## Alternatives

`render.yaml` is a Render Blueprint, but nothing about the service is
Render-specific — it is a stock ASGI app. Any host that runs

```bash
pip install -r api/requirements.txt
cd api && uvicorn main:app --host 0.0.0.0 --port $PORT
```

will serve it: Fly.io, Railway, Google Cloud Run, or a VPS behind nginx. Two
requirements carry over regardless of host:

- **HTTPS is mandatory.** Android blocks cleartext by default, and a browser on
  an HTTPS page refuses to call an `http://` endpoint as mixed content. Both
  clients fail identically against a plain-HTTP API.
- **`engine/` must sit beside `api/`.** `api/main.py` adds `../engine` to
  `sys.path`; flattening the tree breaks the import.
