# Callmark scheduled lead scraper

This repository runs one complete market per invocation. The workflow reads the
next city from the Firestore document `scrape_progress/current`, queries all
configured niches through the repository's optimized gosom fork in a locally
built Docker image with Playwright image/font/media/stylesheet requests blocked,
audits websites with a plain HTTP HTML fetch, filters prospects, deduplicates by
the last ten phone digits, writes
new contacts to the Callmark path `users/{FIREBASE_USER_ID}/contacts`, and then
advances the city index (wrapping after the 50th city).

Proxy isolation is deliberate: only the gosom Docker command receives the
rotating proxy through `-proxies-file`. Website audits use a direct
`requests.Session.get()` call with environment proxy discovery disabled and no
session proxies configured.

The fork source and Dockerfile are under `gosom-google-maps-scraper/`. The
workflow builds `callmark/gosom-google-maps-scraper:text-only` on the runner;
it does not pull the stock gosom scraper image.

Each contact uses Callmark's canonical display fields: `id`, `name`, `contactName`,
`company`, `role`, `phone`, `email`, `city`, `status`, `due`, `dueDate`,
`lastCall`, `lastOutcome`, `notes`, `tag`, and `source`. Scraper-specific fields
such as `website_opportunity`, `website_status`, `address`, and `verified_date`
are retained alongside them.
This mapping matches `MARKING APP/src/firebase.js` (`cleanContact`) and the
`ContactRow`/`DetailPanel` renderers in `MARKING APP/src/main.jsx`.

## GitHub Actions secrets

Add these repository or environment secrets:

| Secret | Value |
| --- | --- |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | The complete Firebase Admin service-account JSON object for the project that owns the Callmark Firestore database. |
| `FIREBASE_SERVICE_ACCOUNT_BASE64` | Optional base64-encoded equivalent of the service-account JSON; use this instead of `FIREBASE_SERVICE_ACCOUNT_JSON` only when multiline secrets are inconvenient. |
| `FIREBASE_USER_ID` | The Firebase Auth UID whose Callmark contacts should receive new leads. This is the `{uid}` in `users/{uid}/contacts`. |
| `PROXY_URL` | Optional complete authenticated rotating-proxy URL. Use this instead of the individual proxy fields below when your provider supplies one. |
| `PROXY_HOST` | Hostname of the rotating proxy gateway. |
| `PROXY_PORT` | Port of the rotating proxy gateway. |
| `PROXY_USERNAME` | Username supplied by the proxy provider. |
| `PROXY_PASSWORD` | Password supplied by the proxy provider. |
| `PROXY_SCHEME` | Optional proxy scheme such as `http` or `socks5`; defaults to `http`. |

`FIREBASE_SERVICE_ACCOUNT_BASE64` is an optional alternative to
`FIREBASE_SERVICE_ACCOUNT_JSON` for systems that cannot store multiline JSON
secrets. When using `PROXY_URL`, the individual proxy fields can be left unset.

The workflow is scheduled for 02:00 UTC every day and can be started with
**Run workflow** from the Actions tab.

The scraper uses a 5 km market radius, 2 km grid cells, and two concurrent
browser jobs by default. Override these with `SCRAPER_RADIUS`,
`SCRAPER_GRID_CELL_KM`, and `SCRAPER_CONCURRENCY` repository/environment
variables when the runner or proxy plan needs different limits.
