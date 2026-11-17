"""Replace unbounded Socrata pagination with a dump-safe, GIL-friendly fetch.

``/api/views/<id>/rows.json`` ignores ``$limit``/``$offset`` and returns the whole
dataset in one response (Quick Draw: 1.7M+ rows), so a cap applied after the
download is too late and the backend runs out of memory. Configured view URLs are
therefore fetched through the SODA resource endpoint ``/resource/<id>.json``,
which honours ``$limit``/``$offset``: newest rows first, pages of at most the
remaining cap, so no response can ever exceed ``INGEST_MAX_ROWS_PER_GAME``.
"""
import os
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from utils.game_data_parser import _extract_rows_and_columns as extract_rows_and_columns_util
from utils.game_validation import tag_rows_with_source

MAX_ROWS = int(os.getenv("INGEST_MAX_ROWS_PER_GAME", "80000"))
_VIEW_RE = re.compile(r"^/api/views/([a-z0-9]{4}-[a-z0-9]{4})/rows\.json$")


def max_rows_for(game) -> int:
    """Row cap for one game, read at call time (env can change after import).

    ``INGEST_MAX_ROWS_<GAME>`` (e.g. ``INGEST_MAX_ROWS_QUICKDRAW``) overrides
    ``INGEST_MAX_ROWS_PER_GAME``.
    """
    specific = os.getenv(f"INGEST_MAX_ROWS_{str(game or '').upper()}")
    raw = specific if specific not in (None, "") else os.getenv("INGEST_MAX_ROWS_PER_GAME", str(MAX_ROWS))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return MAX_ROWS


def pageable_endpoint(endpoint: str) -> str:
    """Map a full-dump view URL to the SODA resource URL that honours $limit/$offset."""
    parsed = urlparse(endpoint)
    match = _VIEW_RE.match(parsed.path or "")
    if not match:
        return endpoint
    return urlunparse(parsed._replace(path=f"/resource/{match.group(1)}.json", query=""))


def install_capped_fetch(ingest_service):
    """Monkey-patch IngestService._fetch_all_rows_from_api."""

    def _fetch_all_rows_from_api(*args, **kwargs):
        return fetch_rows_capped(ingest_service, *args, **kwargs)

    ingest_service._fetch_all_rows_from_api = _fetch_all_rows_from_api
    print(f"Socrata fetch cap installed (max {max_rows_for(None)} rows/game, paged)")


def fetch_rows_capped(ingest_service, game, endpoints, progress_callback=None, total_estimated_game_rows=0):
    """Paged fetch that never requests or keeps more than the game's row cap."""
    cap = max_rows_for(game)
    page_size = max(1, int(getattr(ingest_service, "batch_size", 500) or 500))
    timeout = int(getattr(ingest_service, "request_timeout", 20) or 20)
    max_retries = int(getattr(ingest_service, "max_retries", 2) or 2)
    retry_delay = float(getattr(ingest_service, "retry_delay", 1) or 1)
    progress_interval = float(getattr(ingest_service, "progress_interval_s", 0.5) or 0.5)

    all_rows = []
    column_names = []
    last_progress_at = 0.0
    estimate = min(int(total_estimated_game_rows or 0), cap)

    def emit(fetched):
        nonlocal last_progress_at, estimate
        if fetched > estimate:
            estimate = fetched
        if not progress_callback:
            return
        now = time.time()
        if fetched >= estimate or now - last_progress_at >= progress_interval:
            progress_callback(fetched, estimate or fetched)
            last_progress_at = now

    for endpoint_idx, endpoint in enumerate(endpoints or []):
        if len(all_rows) >= cap:
            break
        fetch_url = pageable_endpoint(endpoint)
        parsed = urlparse(fetch_url)
        paged_resource = fetch_url != endpoint
        filtered = []
        for key, values in parse_qs(parsed.query).items():
            if key.lower() == "accesstype" and any(v.lower() == "download" for v in values):
                continue
            for value in values:
                filtered.append((key, value))
        if paged_resource:
            # Newest rows first, stable order, so the cap keeps the most recent draws.
            filtered.append(("$order", ":id DESC"))

        print(f"[{str(game).upper()}] Endpoint {endpoint_idx + 1}/{len(endpoints)}: {fetch_url} (cap {cap})")
        offset = 0
        while len(all_rows) < cap:
            remaining = cap - len(all_rows)
            limit = min(page_size, remaining)
            params = parse_qs(urlencode(filtered, doseq=True), keep_blank_values=True)
            params["$limit"] = [str(limit)]
            params["$offset"] = [str(offset)]
            page_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

            page_ok = False
            data = None
            for attempt in range(max_retries):
                try:
                    print(f"  Fetching offset={offset} limit={limit} attempt {attempt + 1}/{max_retries}")
                    response = requests.get(page_url, timeout=timeout)
                    response.raise_for_status()
                    data = response.json()
                    page_ok = True
                    break
                except Exception as exc:
                    print(f"  warn: {exc}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (2 ** attempt))
            if not page_ok:
                break

            fetched_rows, extracted_columns = extract_rows_and_columns_util(data)
            # Provenance tag so validation/repair can prove which dataset a row came from.
            fetched_rows = tag_rows_with_source(fetched_rows, endpoint)
            if extracted_columns and not column_names:
                column_names = extracted_columns
            if not fetched_rows:
                break

            ignored_limit = len(fetched_rows) > limit
            if len(fetched_rows) > remaining:
                fetched_rows = fetched_rows[:remaining]

            all_rows.extend(fetched_rows)
            emit(len(all_rows))
            time.sleep(0.05)

            if ignored_limit:
                print(f"  endpoint ignored $limit (got more than {limit} rows); kept {len(fetched_rows)}")
                break
            if len(fetched_rows) < limit:
                break
            offset += len(fetched_rows)

        if len(all_rows) >= cap:
            print(f"  cap {cap} rows reached for {game}")
            break

    return all_rows, column_names
