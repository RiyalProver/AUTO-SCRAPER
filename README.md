# Callmark scheduled lead scraper

This repository runs one complete market per invocation. The workflow reads the
next city from `scrape_progress.json`, queries all configured niches through the
repository's optimized gosom fork in a locally built Docker image with Playwright
using Chromium's native image blocking while retaining its HTTP cache, audits
websites with a plain HTTP HTML fetch, filters prospects, deduplicates by the last
ten phone digits, writes a Callmark-compatible CSV artifact, and then advances the
city index (wrapping after the 50th city). GitHub Actions commits the updated
progress file back to the repo.

Proxy isolation is deliberate: only the gosom Docker command receives the
rotating proxy through `-proxies-file`. Website audits use a direct
`requests.Session.get()` call with environment proxy discovery disabled and no
session proxies configured.

The fork source and Dockerfile are under `gosom-google-maps-scraper/`. The
workflow builds `callmark/gosom-google-maps-scraper:cache-friendly` on the runner;
it does not pull the stock gosom scraper image. The fork does not use Playwright
request routing because routing disables Chromium's HTTP cache.

Each CSV contact uses Callmark's canonical display fields: `id`, `name`, `contactName`,
`company`, `role`, `phone`, `email`, `city`, `status`, `due`, `dueDate`,
`lastCall`, `lastOutcome`, `notes`, `tag`, and `source`. Scraper-specific fields
such as `website_opportunity`, `website_status`, `address`, and `verified_date`
are retained alongside them.
This mapping matches the fields consumed by Callmark's contact import and display.

## GitHub Actions secrets

Add these repository or environment secrets:

| Secret | Value |
| --- | --- |
| `PROXY_HOST` | Hostname of the rotating proxy gateway. |
| `PROXY_PORT` | Port of the rotating proxy gateway. |
| `PROXY_USERNAME` | Username supplied by the proxy provider. |
| `PROXY_PASSWORD` | Password supplied by the proxy provider. |
| `PROXY_SCHEME` | Proxy scheme such as `http` or `socks5`. |

The workflow is scheduled for 02:00 UTC every day and can be started with
**Run workflow** from the Actions tab. Each successful run uploads an artifact
named `callmark-leads-{city}-{state}-{YYYY-MM-DD}` containing that market's CSV;
test runs use the `callmark-test-leads-...` prefix.

For a low-bandwidth end-to-end check, enable the `test_mode` checkbox when
manually dispatching the workflow, run `python lead_pipeline.py --test-mode`, or
set `TEST_MODE=true`. Test mode disables grid coverage, uses depth 1 and
concurrency 1, and retains at most 10 raw Google Maps results per niche before
website audits. The cap is passed into gosom, so it limits place-page visits
rather than downloading every result and trimming afterward. Override it with
`--test-results-per-niche N` or the
`TEST_RESULTS_PER_NICHE` environment variable.

The scraper uses a 5 km market radius, 2 km grid cells, and two concurrent
browser jobs by default. Override these with `SCRAPER_RADIUS`,
`SCRAPER_GRID_CELL_KM`, and `SCRAPER_CONCURRENCY` repository/environment
variables when the runner or proxy plan needs different limits.
