# Netryx web deployment

The web product has two independent deployment units:

1. `frontend/` is a static site suitable for GitHub Pages.
2. `netryx_web/` is a Python API and worker. It must run on a server with the
   model/index storage and, for practical search times, an NVIDIA GPU.

GitHub Pages cannot execute Python, keep secrets, run CUDA, or process jobs.

## Local verification

```bash
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/python -m pytest tests -q
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run check
NETRYX_RESULTS_DIR=/opt/data/cache/netryx .venv/bin/python webapp_server.py
```

Open `http://127.0.0.1:8000`. The bundled frontend uses the same origin by
default.

## Runtime configuration

| Variable | Purpose | Default |
|---|---|---|
| `NETRYX_RESULTS_DIR` | Root containing `<listing-id>/headless/ranked_coordinates.json` | `/opt/data/cache/netryx` |
| `NETRYX_RUNTIME_DIR` | SQLite queue and temporary listing artifacts | `./runtime` |
| `NETRYX_RESEARCH_COMMAND` | Optional override for the headless command; defaults to `netryx_web/live_runner.py` | unset |
| `NETRYX_IMAGERY_PROVIDER` | `panoramax` for open street imagery without a key, or explicit `google` | `panoramax` |
| `GOOGLE_STREETVIEW_API_KEY` | Server-side key for an explicitly authorized official Street View API workflow; the old unauthenticated tile path is disabled | unset |
| `NETRYX_GOOGLE_STREETVIEW_AUTHORIZED` | Must be `true` only after confirming that the exact Street View retrieval, storage, and ML use is licensed | unset |
| `NETRYX_SEARCH_RADIUS_M` | Radius around candidate centers for live street-imagery search | `300` |
| `NETRYX_SYNDICATION_PROVIDER` | `command` (default) or `firecrawl` for the built-in web-search adapter | `command` |
| `NETRYX_SYNDICATION_COMMAND` | Optional terms-compliant search adapter when the provider is `command`; receives JSON on stdin and returns a report on stdout | unset |
| `NETRYX_SYNDICATION_TIMEOUT_S` | Timeout for the syndication adapter | `15` |
| `FIRECRAWL_API_KEY` | Optional server-side Firecrawl key for higher search limits; never expose it to the frontend | unset |
| `NETRYX_MAX_QUERY_IMAGES` | Maximum number of high-confidence exterior photos sent to matching | `4` |
| `NETRYX_CORS_ORIGINS` | Comma-separated allowed frontend origins | local origins |
| `SCRAPFLY_API_KEY` | Optional server-side Leboncoin acquisition fallback | unset |
| `PORT` | API port | `8000` |

Never place `SCRAPFLY_API_KEY`, model credentials, cookies, or provider tokens
in `frontend/js/runtime-config.js`. Static assets are public.

Every fresh listing manifest records a `syndication` report. Its status is one
of `found`, `not_found`, `unavailable`, or `error`; a provider failure is never
converted into `not_found`. Mirror URLs remain evidence only and are not used
as the primary listing source. The report records the provider, queries,
matching basis, and any location hints supplied by a verified candidate. When a
candidate supplies a different commune, the live runner searches the primary
and syndicated location candidates instead of trusting the displayed city.

The live runner also writes `exterior-selection.json`. Only images accepted by
the local exterior classifier are passed to CosPlace/DISK/LightGlue. Interior,
uncertain, unreadable, and duplicate images are rejected; if no image is
accepted, the run stops with `NO_HIGH_CONFIDENCE_EXTERIOR_PHOTOS` rather than
falling back to the first photos.

The provisional ImageNet gate requires locally installed `torch`,
`torchvision`, and `Pillow`, plus a pre-provisioned ResNet-50 checkpoint. It
does not download model weights at runtime and fails closed if the checkpoint
is absent. A calibrated scene classifier is preferable for production.

The web worker defaults to public Panoramax imagery. See
[the photo search guide](OPEN_IMAGERY.md) for local photo input, explicit exterior
review, coverage limits and attribution. The historical Tk GUI is not migrated.
Manual exterior review is available in the browser's **Photos** form and in the CLI.
The desktop Tk GUI remains a separate legacy application; use the web interface
for the Panoramax workflow.

Google's undocumented `GeoPhotoService.SingleImageSearch` and
`cbk0.google.com/cbk?output=tile` routes are not supported acquisition APIs.
Google now rejects the tile route with `403 PERMISSION_DENIED`; moreover,
Google's current Maps Platform terms and Map Tiles policy restrict bulk
prefetching, persistent indexing, and machine-learning analysis of Google
imagery. Configure an official workflow only after confirming that the exact
use is authorized. Otherwise use imagery whose licence explicitly permits
bulk download, storage, and computer-vision analysis.

### Headless command contract

The configured command receives one final argument: an absolute path to a JSON
listing manifest. It is executed without a shell and must print exactly one JSON
object to stdout:

```json
{
  "summary_fr": "Conclusion courte et vérifiable.",
  "location": {
    "label_fr": "Adresse ou zone estimée",
    "latitude": 48.8566,
    "longitude": 2.3522
  },
  "confidence": {"level": "HIGH", "score": null},
  "evidence": [
    {
      "kind": "geometric_match",
      "label_fr": "Correspondances géométriques",
      "detail_fr": "132 correspondances après RANSAC.",
      "value": 132,
      "unit": "inliers"
    }
  ],
  "google_maps_url": "ignored-and-rebuilt-by-the-api"
}
```

The API rebuilds the Google Maps URL from validated coordinates. A non-zero
exit code, invalid JSON, timeout, missing images, or blocked listing produces an
explicit failed/blocked job; the service never fabricates a location.

### Syndication adapter contract

`NETRYX_SYNDICATION_PROVIDER` is separate from `NETRYX_RESEARCH_COMMAND`. Set
it to `firecrawl` to use the built-in adapter against Firecrawl's documented
`POST https://api.firecrawl.dev/v2/search` endpoint, or leave it as `command`
to use an independently managed adapter. The command adapter is executed
without a shell and receives this JSON shape on stdin:

```json
{
  "submitted_url": "https://www.leboncoin.fr/ad/ventes_immobilieres/123",
  "listing_id": "123",
  "title": "...",
  "description": "...",
  "location_hints": ["Displayed Town 00000"],
  "image_sha256": ["..."]
}
```

It must return a report such as:

```json
{
  "status": "found",
  "provider": "provider-name",
  "queries": ["..."],
  "candidates": [
    {
      "result_url": "https://mirror.example/ad/123",
      "title": "...",
      "location_hint": "Nearby Town 00001",
      "match_basis": ["price", "surface", "photos"]
    }
  ]
}
```

The adapter must respect the source sites' terms, robots rules, rate limits,
privacy requirements, and attribution. It must not return a mirror as if it
were the submitted Leboncoin source.

## GitHub Pages

This fork's interface is published at
https://rkanotai.github.io/Netryx-OpenSource-Next-Gen-Street-Level-Geolocation/.
The API origin is `https://netryx-api.cecilebui.com` in the example configuration
`frontend/js/runtime-config.js`. Open **Connexion** and enter the private access
token to submit searches. A browser-specific API override can be cleared with
**Réinitialiser**. For a separate deployment, change the public origin or use
the connection settings; never put the private token in the frontend.

### Browser photo workflow

1. Select **Photos** and choose 1–4 JPEG/PNG files (10 MiB each, 64 pixels minimum
   per dimension and 40 megapixels maximum).
2. Inspect the previews and remove interiors or unsuitable photos.
3. Enter the approximate latitude/longitude and a radius between 50 and 1000 m.
4. Confirm that every selected photo is an exterior; choose HD or SD imagery.
5. Submit and leave the page open while the worker acquires and compares imagery.
6. Inspect the confidence and geometric evidence. The coordinates belong to the
   camera, not a certified building address.

Uploads use `POST /api/v1/photo-geolocations`; both input modes share
`GET /api/v1/geolocations/{job_id}` for polling. Inputs are decoded and re-encoded
without EXIF/GPS metadata, given generated names and hashed server-side. Paths
are not exposed by the job API. The queue defaults to four active jobs and one
GPU worker. Direct input copies are removed after a worker attempt; diagnostic
artifacts and imagery caches remain in the private runtime directory. Do not
publish that directory. Operators must manage disk retention and backups.

### Protecting the public API

Set a long random `NETRYX_API_TOKEN` **before** exposing the API publicly.
It protects all `/api` routes, including polling and schema access. `/healthz`
remains public and reports whether authentication is required. Set
`NETRYX_CORS_ORIGINS=https://rkanotai.github.io`; CORS is not authentication.
Enter the token through **Connexion**. For this private single-user deployment,
the browser stores it in `localStorage`; it remains absent from URLs, source
code, and the Pages artifact. The stored record is bound to the normalized API
origin and is never reused after switching to another API origin. Every script
running on the same GitHub Pages origin can still read it, so use only a personal
browser and click **Effacer le jeton** before sharing the device. Anyone with
this token can access this instance; this is not a multi-tenant service.

Additional settings: `NETRYX_MAX_ACTIVE_JOBS` (default 4),
`NETRYX_PHOTO_RESEARCH_TIMEOUT_S` (default 3600). Requests are bounded before
multipart parsing (42 MiB for photo uploads, 64 KiB for listing JSON), including
chunked bodies. Run a single API process, not multiple GPU-owning workers.

### NAS and Cloudflare Tunnel

A possible deployment uses the hostname `netryx-api.cecilebui.com` through a
named Cloudflare Tunnel. Only configure DNS and tunnel ingress after completing
the live health, CORS, authentication, submission, and job-polling checks below.

Cloudflare Tunnel can connect out from the NAS to an API listening on
`127.0.0.1:8000`, without opening router ports or exposing the NAS administration.
Authenticate `cloudflared tunnel login` in the operator's browser, create a named
tunnel, then create its DNS route and a hostname-specific ingress rule. Keep
Cloudflare certificates and tunnel credentials outside Git. Use an explicit
final `http_status:404` ingress rule, not a catch-all proxy to the application.

Do not set the Pages API origin until `/healthz`, authenticated job submission,
polling, and the real Pages CORS origin work over HTTPS. A temporary quick-tunnel
URL is not a permanent deployment. The NAS administrator must ensure the API
and tunnel start automatically after a NAS/container restart; a background
process launched from a terminal alone does not provide reboot persistence.

#### Process supervision on this machine

`deploy/start.sh` runs Supervisor in the foreground. The API is restarted if it
crashes, with rotating private logs and child-process-group termination. It
binds only to loopback. It uses `/opt/data/services/netryx/api.env` (mode 0600)
for the private API token, CORS and runtime configuration. The Supervisor
environment is `/opt/data/services/netryx/supervisor-venv`.

The tunnel configuration is `/opt/data/services/netryx/tunnel.yml`. Cloudflare
credentials stay outside the repository in the current user's `.cloudflared`
directory. The private API token is the `NETRYX_API_TOKEN` value in `api.env`;
copy it locally into the site's **Connexion → Jeton d’accès** field. Do not
paste it into chat, commit it, or share the environment file.

The mutable application checkout is mapped from `/mnt/Serveur/Hermes` to
`/opt/data`. Keep s6 definitions outside that writable tree. A TrueNAS root
administrator must obtain the definitions from a separate root-owned GitHub
checkout at `/mnt/Serveur/NetryxS6/source`, using the reviewed repository and
commit:

```bash
git clone https://github.com/RKANOTAI/Netryx-OpenSource-Next-Gen-Street-Level-Geolocation.git \
  /mnt/Serveur/NetryxS6/source
git -C /mnt/Serveur/NetryxS6/source checkout 4df225cb355ac347b71cde09b172d53b5e47047d
chown -R root:root /mnt/Serveur/NetryxS6/source
```

Install the definitions from that checkout as root. The run file and service
folders are 0755; the type, dependency, and bundle files are 0644:

```bash
install -d -o root -g root -m 0755 \
  /mnt/Serveur/NetryxS6/s6-rc.d/netryx/dependencies.d \
  /mnt/Serveur/NetryxS6/user-contents.d
install -o root -g root -m 0755 \
  /mnt/Serveur/NetryxS6/source/deploy/s6-overlay/s6-rc.d/netryx/run \
  /mnt/Serveur/NetryxS6/s6-rc.d/netryx/run
install -o root -g root -m 0644 \
  /mnt/Serveur/NetryxS6/source/deploy/s6-overlay/s6-rc.d/netryx/type \
  /mnt/Serveur/NetryxS6/s6-rc.d/netryx/type
install -o root -g root -m 0644 \
  /mnt/Serveur/NetryxS6/source/deploy/s6-overlay/s6-rc.d/netryx/dependencies.d/base \
  /mnt/Serveur/NetryxS6/s6-rc.d/netryx/dependencies.d/base
install -o root -g root -m 0644 \
  /mnt/Serveur/NetryxS6/source/deploy/s6-overlay/user-contents.d/netryx \
  /mnt/Serveur/NetryxS6/user-contents.d/netryx
```

In **Apps → Hermes → Edit → Additional Storage**, add exactly these read-only
Host Path mounts before restarting the app:

| Host source | Container destination | Mode |
|---|---|---|
| `/mnt/Serveur/NetryxS6/s6-rc.d/netryx` | `/etc/s6-overlay/s6-rc.d/netryx` | read-only |
| `/mnt/Serveur/NetryxS6/user-contents.d/netryx` | `/etc/s6-overlay/s6-rc.d/user/contents.d/netryx` | read-only |

Mount only the service directory and the individual bundle file above. Do not
mount the whole `s6-rc.d` or `contents.d` directory, and do not substitute a
file from `/mnt/Serveur/Hermes` or `/opt/data`. If the TrueNAS form refuses the
individual bundle-file mount, stop rather than mounting its parent. s6 compiles
these definitions during container startup, so restart the container after
changing them.

At startup s6 launches the root-owned `netryx/run` definition. Its only
root-privileged action is executing the trusted root-owned
`/command/s6-setuidgid hermes`. That immediately drops privileges; only then
are the mutable `/opt/data` `supervisord` binary, supervisor configuration,
environment file, repository scripts, logs, and tunnel configuration opened.
The version-controlled run definition therefore invokes Supervisor directly as `hermes`
with `HOME=/opt/data` and the `NETRYX_*` paths required by the repository.

```bash
# API only, until the named tunnel has been authorized and configured:
sh deploy/start.sh

# Once /opt/data/services/netryx/tunnel.yml exists and is configured:
NETRYX_TUNNEL_ENABLED=true sh deploy/start.sh
```

Run only one supervisor. To query it without exposing any secrets:

```bash
/opt/data/services/netryx/supervisor-venv/bin/supervisorctl \
  -s unix:///opt/data/services/netryx/supervisor.sock status
```

After restart, verify `s6-svstat /run/service/netryx`, Supervisor status, local
`/healthz`, then the public HTTPS endpoint. Do not open router ports or forward
Internet traffic to the NAS administration interface.

1. Fork the upstream repository; do not push to `rushowr/...` directly.
2. Set `frontend/js/runtime-config.js` `apiBase` to the deployed HTTPS API.
3. Add the Pages origin to `NETRYX_CORS_ORIGINS`, for example:

   ```text
   https://YOUR_USER.github.io
   ```

4. In the fork, set **Settings → Pages → Source** to **GitHub Actions**.
5. Push or merge to the fork's default `main` branch.
6. Verify the `Deploy web interface to GitHub Pages` workflow and fetch the
   environment URL over HTTPS.

The usual project URL is:

```text
https://YOUR_USER.github.io/Netryx-OpenSource-Next-Gen-Street-Level-Geolocation/
```

## API checks after deployment

```bash
curl --fail https://API_HOST/healthz
curl -i -X OPTIONS \
  -H 'Origin: https://YOUR_USER.github.io' \
  -H 'Access-Control-Request-Method: POST' \
  https://API_HOST/api/v1/geolocations
```

Then submit one listing from the Pages UI and verify the exact job through
`GET /api/v1/geolocations/{job_id}`. A green health check alone does not prove
the source acquisition or GPU research path works.
