"""Scheduled Google Maps lead scraping pipeline for the Callmark app.

The script intentionally performs one market per invocation. GitHub Actions runs it
on a schedule, while a repository JSON file selects the next market. Qualified leads
are written to a Callmark-compatible CSV artifact for later import.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse

import requests


LOG = logging.getLogger("lead_pipeline")


NICHES: tuple[str, ...] = (
    "electricians",
    "plumbers",
    "HVAC contractors",
    "roofing contractors",
    "house cleaning services",
    "carpet cleaning services",
    "window cleaning services",
    "janitorial services",
    "independent non-chain restaurants",
)

CITIES: tuple[tuple[str, str], ...] = (
    ("Fort Wayne", "IN"), ("Evansville", "IN"), ("South Bend", "IN"),
    ("Elkhart", "IN"), ("Lafayette", "IN"), ("Terre Haute", "IN"),
    ("Bloomington", "IN"), ("Muncie", "IN"), ("Kokomo", "IN"),
    ("Columbus", "IN"), ("Anderson", "IN"), ("Michigan City", "IN"),
    ("Marion", "IN"), ("Richmond", "IN"), ("Jeffersonville", "IN"),
    ("Pueblo", "CO"), ("Grand Junction", "CO"), ("Greeley", "CO"),
    ("Loveland", "CO"), ("Longmont", "CO"), ("Castle Rock", "CO"),
    ("Montrose", "CO"), ("Durango", "CO"), ("Glenwood Springs", "CO"),
    ("Cañon City", "CO"), ("Parker", "CO"), ("Brighton", "CO"),
    ("Steamboat Springs", "CO"), ("Sterling", "CO"), ("Idaho Falls", "ID"),
    ("Pocatello", "ID"), ("Twin Falls", "ID"), ("Coeur d'Alene", "ID"),
    ("Nampa", "ID"), ("Caldwell", "ID"), ("Lewiston", "ID"),
    ("Post Falls", "ID"), ("Rexburg", "ID"), ("Sandpoint", "ID"),
    ("Mountain Home", "ID"), ("Charleston", "WV"), ("Huntington", "WV"),
    ("Morgantown", "WV"), ("Parkersburg", "WV"), ("Wheeling", "WV"),
    ("Martinsburg", "WV"), ("Fairmont", "WV"), ("Beckley", "WV"),
    ("Clarksburg", "WV"), ("Weirton", "WV"),
)

# Keep the source ASCII-safe while repairing the legacy mojibake spelling that
# existed in the checked-in market list.
def _repair_mojibake(value: str) -> str:
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


CITIES = tuple((_repair_mojibake(city), state) for city, state in CITIES)

if len(CITIES) != 50:
    raise RuntimeError(f"Expected exactly 50 rotating markets, found {len(CITIES)}")

# Approximate city centres used only to seed the scraper's coverage grid. The
# grid extends by SCRAPER_RADIUS in every direction, so results are collected
# across the full market rather than only from Google's first result page.
CITY_CENTERS: dict[tuple[str, str], tuple[float, float]] = {
    ("Fort Wayne", "IN"): (41.0793, -85.1394),
    ("Evansville", "IN"): (37.9716, -87.5711),
    ("South Bend", "IN"): (41.6764, -86.2520),
    ("Elkhart", "IN"): (41.6820, -85.9767),
    ("Lafayette", "IN"): (40.4167, -86.8753),
    ("Terre Haute", "IN"): (39.4667, -87.4139),
    ("Bloomington", "IN"): (39.1653, -86.5264),
    ("Muncie", "IN"): (40.1934, -85.3864),
    ("Kokomo", "IN"): (40.4864, -86.1336),
    ("Columbus", "IN"): (39.2014, -85.9214),
    ("Anderson", "IN"): (40.1053, -85.6803),
    ("Michigan City", "IN"): (41.7075, -86.8950),
    ("Marion", "IN"): (40.5584, -85.6591),
    ("Richmond", "IN"): (39.8289, -84.8902),
    ("Jeffersonville", "IN"): (38.2776, -85.7372),
    ("Pueblo", "CO"): (38.2544, -104.6091),
    ("Grand Junction", "CO"): (39.0639, -108.5506),
    ("Greeley", "CO"): (40.4233, -104.7091),
    ("Loveland", "CO"): (40.3978, -105.0750),
    ("Longmont", "CO"): (40.1672, -105.1019),
    ("Castle Rock", "CO"): (39.3722, -104.8561),
    ("Montrose", "CO"): (38.4783, -107.8762),
    ("Durango", "CO"): (37.2753, -107.8801),
    ("Glenwood Springs", "CO"): (39.5505, -107.3248),
    ("Cañon City", "CO"): (38.4404, -105.2424),
    ("Parker", "CO"): (39.5186, -104.7614),
    ("Brighton", "CO"): (39.9853, -104.8205),
    ("Steamboat Springs", "CO"): (40.4850, -106.8317),
    ("Sterling", "CO"): (40.6255, -103.2077),
    ("Idaho Falls", "ID"): (43.4917, -112.0330),
    ("Pocatello", "ID"): (42.8713, -112.4455),
    ("Twin Falls", "ID"): (42.5558, -114.4701),
    ("Coeur d'Alene", "ID"): (47.6777, -116.7805),
    ("Nampa", "ID"): (43.5407, -116.5635),
    ("Caldwell", "ID"): (43.6629, -116.6874),
    ("Lewiston", "ID"): (46.4004, -117.0012),
    ("Post Falls", "ID"): (47.7170, -116.9516),
    ("Rexburg", "ID"): (43.8260, -111.7897),
    ("Sandpoint", "ID"): (48.2766, -116.5530),
    ("Mountain Home", "ID"): (43.1329, -115.6912),
    ("Charleston", "WV"): (38.3362, -81.6123),
    ("Huntington", "WV"): (38.4192, -82.4452),
    ("Morgantown", "WV"): (39.6295, -79.9559),
    ("Parkersburg", "WV"): (39.2667, -81.5615),
    ("Wheeling", "WV"): (40.0640, -80.7209),
    ("Martinsburg", "WV"): (39.4562, -77.9639),
    ("Fairmont", "WV"): (39.4851, -80.1426),
    ("Beckley", "WV"): (37.7782, -81.1882),
    ("Clarksburg", "WV"): (39.2806, -80.3445),
    ("Weirton", "WV"): (40.4187, -80.5895),
}

CITY_CENTERS = {
    (_repair_mojibake(city), state): coordinates
    for (city, state), coordinates in CITY_CENTERS.items()
}
if set(CITIES) != set(CITY_CENTERS):
    raise RuntimeError("Every rotating market must have a grid centre coordinate")


def city_grid_bbox(market: "Market", radius_m: float) -> str:
    """Return a valid gosom -grid-bbox around a market centre."""
    try:
        radius_km = float(radius_m) / 1000.0
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid grid radius: {radius_m!r}") from exc
    if radius_km <= 0:
        raise ValueError("Grid radius must be greater than zero")
    try:
        latitude, longitude = CITY_CENTERS[(market.city, market.state)]
    except KeyError as exc:
        raise ValueError(f"No grid centre configured for {market.label}") from exc
    latitude_delta = radius_km / 111.32
    longitude_delta = radius_km / (111.32 * max(abs(math.cos(math.radians(latitude))), 1e-6))
    return ",".join(
        f"{value:.6f}"
        for value in (
            latitude - latitude_delta,
            longitude - longitude_delta,
            latitude + latitude_delta,
            longitude + longitude_delta,
        )
    )

# Matching is case-insensitive and ignores punctuation/whitespace. The list is
# deliberately broader than the examples so national chains do not enter the
# independent-restaurant segment.
RESTAURANT_CHAIN_BLOCKLIST: tuple[str, ...] = (
    "McDonald's", "McDonalds", "Subway", "Domino's", "Dominos", "Chipotle",
    "Starbucks", "Taco Bell", "KFC", "Wendy's", "Wendys", "Dunkin",
    "Pizza Hut", "Applebee's", "Applebees", "IHOP", "Denny's", "Dennys",
    "Burger King", "Walmart", "Sonic Drive-In", "Sonic", "Arby's", "Arbys",
    "Jack in the Box", "Panda Express", "Popeyes", "Little Caesars",
    "Papa John's", "Papa Johns", "Panera Bread", "Chick-fil-A", "Chick fil A",
    "Five Guys", "Whataburger", "In-N-Out", "Culver's", "Culvers",
    "Olive Garden", "Red Lobster", "Outback Steakhouse", "Buffalo Wild Wings",
    "Texas Roadhouse", "Cracker Barrel", "LongHorn Steakhouse", "Dairy Queen",
    "Baskin-Robbins", "Dairy Queen", "Jimmy John's", "Jersey Mike's",
    "Firehouse Subs", "Papa Murphy's", "Wingstop", "Raising Cane's",
    "Tim Hortons", "P.F. Chang's", "P.F. Changs", "The Cheesecake Factory",
    "Chili's", "Chilis", "Ruby Tuesday", "TGI Fridays", "Bojangles",
    "Del Taco", "Zaxby's", "Zaxbys", "Golden Corral", "Carrabba's",
    "Carrabbas", "Hardee's", "Hardees", "Carl's Jr", "Carls Jr", "Moe's",
    "Moes", "Qdoba", "Jamba", "Smoothie King", "Auntie Anne's",
    "Auntie Annes", "Cinnabon", "Wetzel's Pretzels", "Wetzel Pretzels",
    "Which Wich", "Church's Chicken", "Churchs Chicken", "Einstein Bros",
    "Famous Dave's", "Famous Daves", "Hooters", "Twin Peaks", "Perkins",
    "Waffle House", "Cracker Barrel", "Steak 'n Shake", "Steak n Shake",
)

OPPORTUNITY_SOLID = "No obvious homepage technical issue detected"
NO_WEBSITE = "No website listed on Google Maps"
HTTP_ONLY = "HTTP-only website URL"
AUDIT_FAILED = "Homepage unavailable or blocked during audit"
FREE_DOMAIN = "Free page builder domain"
MISSING_META = "Missing meta description"

# Mirrors the Callmark contact display/import fields, including the numeric `id`
# used for selection and avatar rendering.
CALLMARK_CONTACT_FIELDS: tuple[str, ...] = (
    "id", "name", "contactName", "company", "role", "phone", "email", "city",
    "status", "due", "dueDate", "lastCall", "lastOutcome", "notes", "tag",
    "source",
)

# CSV columns accepted by Callmark's contact import, followed by the scraper
# metadata that helps prioritize website-improvement prospects.
CALLMARK_CSV_FIELDS: tuple[str, ...] = CALLMARK_CONTACT_FIELDS + (
    "website_status", "website_opportunity", "address", "rating", "verified_date",
)

PROGRESS_FILE = Path(__file__).with_name("scrape_progress.json")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("output")
DEFAULT_TEST_RESULTS_PER_NICHE = 10
TEST_INPUT_ID_PREFIX = "test-niche-"


@dataclass(frozen=True)
class Market:
    city: str
    state: str

    @property
    def label(self) -> str:
        return f"{self.city}, {self.state}"


def normalize_phone(value: Any) -> str:
    """Normalize a US phone to the last ten digits used by Callmark."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def is_chain_restaurant(name: str, category: str = "") -> bool:
    haystack = normalize_name(f"{name} {category}")
    return any(normalize_name(chain) in haystack for chain in RESTAURANT_CHAIN_BLOCKLIST)


def _first(row: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    lowered = {str(k).casefold().replace("-", "_"): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key.casefold().replace("-", "_"))
        if value not in (None, ""):
            return value
    return default


def _email_value(row: Mapping[str, Any]) -> str:
    """Return the first usable email from gosom's scalar or array output."""
    value = _first(row, "email", "email_address", "emails", default="")
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if str(item).strip()), "")
    return str(value).strip()


class _MetaDescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_description = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        attrs_map = {str(k).casefold(): str(v or "") for k, v in attrs}
        if attrs_map.get("name", "").casefold() == "description" and attrs_map.get("content", "").strip():
            self.has_description = True


def has_meta_description(markup: str) -> bool:
    parser = _MetaDescriptionParser()
    try:
        parser.feed(markup[:2_000_000])
    except Exception:
        return False
    return parser.has_description


def _host_is_free_or_social(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    is_domain = lambda domain: host == domain or host.endswith(f".{domain}")
    return (
        is_domain("wixsite.com")
        or is_domain("weebly.com")
        or is_domain("godaddysites.com")
        or is_domain("business.site")
        or host in {"facebook.com", "m.facebook.com", "instagram.com", "www.facebook.com", "www.instagram.com"}
        or is_domain("facebook.com")
        or is_domain("instagram.com")
    )


def audit_website(url: Any, session: requests.Session | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """Audit raw homepage HTML directly, without any proxy or browser."""
    raw = str(url or "").strip()
    if not raw:
        return {"website": "", "website_final_url": "", "website_status": "NO_WEBSITE", "website_opportunity": NO_WEBSITE}
    target = raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}"
    client = session or requests.Session()
    # Website audits are intentionally direct connections. Disable Requests'
    # ambient HTTP(S)_PROXY lookup and any proxy configured on a reused session;
    # the rotating proxy is reserved for the gosom Docker scraper below.
    client.trust_env = False
    if hasattr(client, "proxies"):
        client.proxies = {}
    try:
        response = client.get(
            target,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": "CallmarkLeadAudit/1.0 (+scheduled crawler)"},
        )
        final_url = str(response.url or target)
        status = response.status_code
        if not (200 <= status < 400):
            opportunity = AUDIT_FAILED
        elif _host_is_free_or_social(final_url):
            opportunity = FREE_DOMAIN
        elif urlparse(final_url).scheme.casefold() != "https":
            opportunity = HTTP_ONLY
        elif not has_meta_description(response.text or ""):
            opportunity = MISSING_META
        else:
            opportunity = OPPORTUNITY_SOLID
        return {
            "website": raw,
            "website_final_url": final_url,
            "website_status": status,
            "website_opportunity": opportunity,
        }
    except Exception:
        return {
            "website": raw,
            "website_final_url": "",
            "website_status": "CHECK_FAILED",
            "website_opportunity": AUDIT_FAILED,
        }


def _json_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("results", "places", "businesses", "data", "items"):
            if key in value:
                records = _json_records(value[key])
                if records:
                    return records
        return [value]
    return []


def load_scraper_results(path: Path) -> list[dict[str, Any]]:
    """Load gosom JSON, NDJSON, or CSV output."""
    if not path.exists():
        raise FileNotFoundError(f"Scraper did not create {path.name}")
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        parsed = json.loads(raw)
        records = _json_records(parsed)
        if records:
            return records
    except json.JSONDecodeError:
        pass
    lines = [line for line in raw.splitlines() if line.strip()]
    ndjson: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                ndjson.append(item)
        except json.JSONDecodeError:
            ndjson = []
            break
    if ndjson:
        return ndjson
    return list(csv.DictReader(io.StringIO(raw)))


def proxy_uri_from_env(env: Mapping[str, str] | None = None) -> str:
    values = env or os.environ
    host = str(values.get("PROXY_HOST", "")).strip()
    port = str(values.get("PROXY_PORT", "")).strip()
    username = str(values.get("PROXY_USERNAME", "")).strip()
    password = str(values.get("PROXY_PASSWORD", "")).strip()
    scheme = str(values.get("PROXY_SCHEME", "")).strip()
    if not all((host, port, username, password, scheme)):
        raise RuntimeError(
            "Set PROXY_HOST, PROXY_PORT, PROXY_USERNAME, PROXY_PASSWORD, and PROXY_SCHEME"
        )
    return f"{scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"


def _safe_error(output: str, proxy_uri: str) -> str:
    if not output:
        return ""
    redacted = output.replace(proxy_uri, "<proxy>")
    return redacted[-2000:]


def _limit_test_rows(rows: Sequence[Mapping[str, Any]], per_niche: int) -> list[dict[str, Any]]:
    """Keep at most ``per_niche`` raw scraper rows for each test query."""
    if per_niche < 1:
        raise ValueError("Test results per niche must be greater than zero")
    counts: dict[str, int] = {}
    limited: list[dict[str, Any]] = []
    for row in rows:
        input_id = str(_first(row, "input_id", default="")).strip()
        key = input_id if input_id.startswith(TEST_INPUT_ID_PREFIX) else "unclassified"
        if counts.get(key, 0) >= per_niche:
            continue
        counts[key] = counts.get(key, 0) + 1
        limited.append(dict(row))
    return limited


def run_gosom_scraper(
    market: Market,
    proxy_uri: str,
    image: str | None = None,
    test_mode: bool = False,
    test_results_per_niche: int = DEFAULT_TEST_RESULTS_PER_NICHE,
) -> list[dict[str, Any]]:
    """Run gosom/google-maps-scraper in Docker with grid coverage for one market."""
    scraper_image = image or os.getenv("GOOGLE_MAPS_SCRAPER_IMAGE", "callmark/gosom-google-maps-scraper:cache-friendly")
    radius_m = float(os.getenv("SCRAPER_RADIUS", "5000"))
    grid_bbox = city_grid_bbox(market, radius_m)
    grid_cell_km = os.getenv("SCRAPER_GRID_CELL_KM", "2")
    concurrency = os.getenv("SCRAPER_CONCURRENCY", "2")
    with tempfile.TemporaryDirectory(prefix="callmark-scrape-") as temp_name:
        temp = Path(temp_name)
        query_file = temp / "queries.txt"
        proxy_file = temp / "proxies.txt"
        queries = []
        for index, niche in enumerate(NICHES):
            query = f"{niche} in {market.label}"
            if test_mode:
                query = f"{query} #!# {TEST_INPUT_ID_PREFIX}{index}"
            queries.append(query)
        query_file.write_text("\n".join(queries) + "\n", encoding="utf-8")
        proxy_file.write_text(proxy_uri + "\n", encoding="utf-8")
        depth = "1" if test_mode else os.getenv("SCRAPER_DEPTH", "5")
        effective_concurrency = "1" if test_mode else concurrency
        command = [
            "docker", "run", "--rm", "--shm-size=2g", "-v", f"{temp}:/data",
            "-v", f"{proxy_file}:/run/secrets/gmaps-proxies:ro", scraper_image,
            "-input", "/data/queries.txt", "-results", "/data/results", "-json",
            "-c", effective_concurrency,
            "-depth", depth,
            "-zoom", os.getenv("SCRAPER_ZOOM", "14"), "-radius", str(radius_m),
            "-lang", "en", "-proxies-file", "/run/secrets/gmaps-proxies",
        ]
        if not test_mode:
            # gosom's grid mode is selected with -grid-bbox; -grid is not a
            # valid flag in the fork and would make the nightly run fail.
            command.extend(["-grid-bbox", grid_bbox, "-grid-cell", grid_cell_km])
        else:
            command.extend(["-max-results-per-query", str(test_results_per_niche)])
        extra = os.getenv("SCRAPER_EXTRA_ARGS", "").strip()
        if extra and not test_mode:
            command.extend(extra.split())
        elif extra:
            LOG.warning("Ignoring SCRAPER_EXTRA_ARGS in test mode to preserve the low-bandwidth limits")
        LOG.info(
            "Running gosom scraper for %s (%d niches, test_mode=%s)",
            market.label,
            len(NICHES),
            test_mode,
        )
        completed = subprocess.run(command, capture_output=True, text=True, timeout=int(os.getenv("SCRAPER_TIMEOUT_SECONDS", "3300")))
        if completed.returncode != 0:
            detail = _safe_error(completed.stderr or completed.stdout, proxy_uri)
            raise RuntimeError(f"gosom scraper failed with exit code {completed.returncode}: {detail}")
        candidates = [
            path for path in temp.rglob("*")
            if path.is_file() and path.name != query_file.name
            and path.suffix.casefold() in {".json", ".csv", ".ndjson", ""}
        ]
        if not candidates:
            raise FileNotFoundError("gosom completed without producing a JSON or CSV results file")
        # Prefer JSON/NDJSON, then CSV, while allowing image versions to choose
        # their own filename or extension under the mounted results path.
        candidates.sort(key=lambda path: (path.suffix.casefold() not in {".json", ".ndjson"}, str(path)))
        rows = load_scraper_results(candidates[0])
        return _limit_test_rows(rows, test_results_per_niche) if test_mode else rows


def _coerce_rating(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value or "")


def _coerce_reviews(value: Any) -> Any:
    try:
        return int(re.sub(r"[^0-9]", "", str(value)))
    except (TypeError, ValueError):
        return str(value or "")


def normalize_scraped_lead(row: Mapping[str, Any], market: Market, niche: str, session: requests.Session | None = None) -> dict[str, Any] | None:
    company = str(_first(row, "company_name", "company", "business_name", "business", "name", "title")).strip()
    if not company:
        return None
    category = str(_first(row, "category", "type", "industry", default=niche)).strip() or niche
    if "restaurant" in f"{niche} {category}".casefold() and is_chain_restaurant(company, category):
        return None
    phone = normalize_phone(_first(row, "phone", "phone_number", "telephone", "mobile"))
    if not phone:
        return None
    website = _first(row, "website", "web_site", "website_url", "site", "url")
    audit = audit_website(website, session=session)
    if audit["website_opportunity"] == OPPORTUNITY_SOLID:
        return None
    source_url = str(_first(row, "source_url", "place_url", "maps_url", "google_maps_url", "link", default="")).strip()
    if not source_url:
        source_url = f"https://www.google.com/maps/search/{quote(company + ' ' + market.label)}"
    verified_date = datetime.now(timezone.utc).date().isoformat()
    return {
        "company_name": company,
        "category": category,
        "market": market.label,
        "city": market.city,
        "state": market.state,
        "phone": phone,
        "email": _email_value(row),
        "website": audit["website"],
        "website_final_url": audit["website_final_url"],
        "website_status": audit["website_status"],
        "website_opportunity": audit["website_opportunity"],
        "address": str(_first(row, "address", "formatted_address", "location", default="")).strip(),
        "rating": _coerce_rating(_first(row, "rating", "review_rating", "stars", default="")),
        "review_count": _coerce_reviews(_first(row, "review_count", "reviews", "reviews_count", default="")),
        "source_url": source_url,
        "source": "gosom/google-maps-scraper",
        "verified_date": verified_date,
    }


def _contact_id(phone: Any) -> int:
    normalized = normalize_phone(phone)
    if not normalized:
        raise ValueError("A normalized phone number is required for a Callmark contact ID")
    # Callmark uses `contact.id % 5` for avatar styling, so IDs must remain
    # numeric. A normalized phone is stable, unique within this pipeline, and
    # safely below JavaScript's maximum precise integer.
    return int(normalized)


def to_callmark_contact(lead: Mapping[str, Any]) -> dict[str, Any]:
    """Map lead metadata into the fields used by the Callmark React app."""
    company = str(lead.get("company_name", "")).strip()
    city = str(lead.get("city", "")).strip()
    state = str(lead.get("state", "")).strip()
    location = str(lead.get("market", "")).strip() or ", ".join(part for part in (city, state) if part)
    return {
        # Keep every requested scraper/audit field, then explicitly overlay the
        # canonical Callmark fields so source data cannot replace their mapped
        # and normalized values.
        **dict(lead),
        "id": _contact_id(lead.get("phone")),
        "name": company,
        "contactName": "",
        "company": company,
        "role": str(lead.get("category", "")).strip(),
        "phone": normalize_phone(lead.get("phone")),
        "email": str(lead.get("email", "")).strip(),
        "city": location,
        "status": "New",
        "due": "Today",
        "dueDate": datetime.now(timezone.utc).date().isoformat(),
        "lastCall": "-",
        "lastOutcome": "Not called yet",
        "notes": "",
        "tag": "Automated lead scraper",
        "source": str(lead.get("source", "gosom/google-maps-scraper")),
    }


def read_progress(path: Path = PROGRESS_FILE) -> int:
    """Read the next city index from the repository progress file."""
    if not path.exists():
        return 0
    try:
        value = (json.loads(path.read_text(encoding="utf-8")) or {}).get("index", 0)
    except (OSError, json.JSONDecodeError, AttributeError):
        return 0
    try:
        index = int(value)
    except (TypeError, ValueError):
        return 0
    return index if 0 <= index < len(CITIES) else 0


def write_progress(index: int, path: Path = PROGRESS_FILE) -> None:
    """Persist only the next city index for the following scheduled run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"index": index % len(CITIES)}, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, contacts: Sequence[Mapping[str, Any]]) -> None:
    """Write Callmark import fields and selected scraper metadata to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CALLMARK_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for contact in contacts:
            writer.writerow({field: contact.get(field, "") for field in CALLMARK_CSV_FIELDS})


def process_results(rows: Iterable[Mapping[str, Any]], market: Market) -> list[dict[str, Any]]:
    session = requests.Session()
    leads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        # gosom includes the originating search query on most versions; use it to
        # associate a result with one of this run's niches when available.
        query = str(_first(row, "query", "search_query", default="")).casefold()
        niche = next((candidate for candidate in NICHES if candidate.casefold() in query), NICHES[0])
        lead = normalize_scraped_lead(row, market, niche, session=session)
        if not lead or lead["phone"] in seen:
            continue
        seen.add(lead["phone"])
        leads.append(lead)
    return leads


def run_once(
    progress_path: Path = PROGRESS_FILE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    test_mode: bool = False,
    test_results_per_niche: int = DEFAULT_TEST_RESULTS_PER_NICHE,
) -> dict[str, Any]:
    current_index = read_progress(progress_path)
    market = Market(*CITIES[current_index])
    proxy_uri = proxy_uri_from_env()
    rows = run_gosom_scraper(
        market,
        proxy_uri,
        test_mode=test_mode,
        test_results_per_niche=test_results_per_niche,
    )
    leads = process_results(rows, market)
    contacts = [to_callmark_contact(lead) for lead in leads]
    scraped_date = datetime.now(timezone.utc).date().isoformat()
    safe_market = re.sub(r"[^a-z0-9]+", "-", market.label.casefold()).strip("-")
    output_prefix = "callmark-test-leads" if test_mode else "callmark-leads"
    output_path = output_dir / f"{output_prefix}-{safe_market}-{scraped_date}.csv"
    write_csv(output_path, contacts)
    stats = {
        "scraped_rows": len(rows),
        "qualified_leads": len(leads),
        "new_leads": len(leads),
        "test_mode": test_mode,
        "output_csv": str(output_path),
    }
    next_index = (current_index + 1) % len(CITIES)
    write_progress(next_index, progress_path)
    LOG.info("Completed %s: %s", market.label, json.dumps(stats, sort_keys=True))
    return {
        "market": market.label,
        "scraped_date": scraped_date,
        "artifact_name": f"{output_prefix}-{safe_market}-{scraped_date}",
        **stats,
        "next_index": next_index,
    }


def write_github_outputs(result: Mapping[str, Any]) -> None:
    """Expose artifact metadata to later GitHub Actions steps when available."""
    output_file = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_file:
        return
    with Path(output_file).open("a", encoding="utf-8") as handle:
        for key in ("artifact_name", "output_csv", "market", "scraped_date"):
            handle.write(f"{key}={result[key]}\n")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-mode",
        action="store_true",
        default=_env_flag("TEST_MODE"),
        help="disable grid mode, use depth/concurrency 1, and keep a small sample per niche",
    )
    parser.add_argument(
        "--test-results-per-niche",
        type=int,
        default=int(os.getenv("TEST_RESULTS_PER_NICHE", str(DEFAULT_TEST_RESULTS_PER_NICHE))),
        help="maximum raw results retained per niche in test mode (default: 10)",
    )
    args = parser.parse_args(argv)
    if args.test_results_per_niche < 1:
        parser.error("--test-results-per-niche must be greater than zero")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        result = run_once(
            test_mode=args.test_mode,
            test_results_per_niche=args.test_results_per_niche,
        )
    except Exception:
        LOG.exception("Lead scrape failed; progress was not advanced")
        return 1
    write_github_outputs(result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
