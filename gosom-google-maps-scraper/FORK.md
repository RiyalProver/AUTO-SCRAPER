# Callmark gosom fork

This directory is a source fork of `gosom/google-maps-scraper` at upstream
commit `2b8616d0ccf7d3c578b42a7440e3c13bb22e5083`.

The fork vendors `gosom/scrapemate` under `third_party/scrapemate` and uses a
local Go module replacement. Playwright launches Chromium with
`--blink-settings=imagesEnabled=false`, which prevents image loading while
preserving Chromium's HTTP cache. The fork deliberately does not install a
global Playwright request route because routing disables that cache and causes
Google Maps scripts to be downloaded again on later place-page navigations.

Build the image from this directory:

```sh
docker build -t callmark/gosom-google-maps-scraper:cache-friendly .
```
