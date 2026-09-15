# Callmark gosom fork

This directory is a source fork of `gosom/google-maps-scraper` at upstream
commit `2b8616d0ccf7d3c578b42a7440e3c13bb22e5083`.

The fork vendors `gosom/scrapemate` under `third_party/scrapemate` and uses a
local Go module replacement. Its Playwright browser context installs a
text-only route that aborts `image`, `font`, `media`, and `stylesheet` requests
while continuing documents, scripts, XHR/fetch, and other text-bearing traffic.
The route is installed on initial and recreated browser contexts.

Build the image from this directory:

```sh
docker build -t callmark/gosom-google-maps-scraper:text-only .
```

