"""Replace unbounded Socrata pagination with a dump-safe, GIL-friendly fetch."""
import os
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from utils.game_data_parser import _extract_rows_and_columns as extract_rows_and_columns_util

MAX_ROWS = int(os.getenv("INGEST_MAX_ROWS_PER_GAME", "80000"))


def install_capped_fetch(ingest_service):
    """Monkey-patch IngestService._fetch_all_rows_from_api."""

    def _fetch_all_rows_from_api(
        game,
        endpoints,
        progress_callback=None,
        total_estimated_game_rows=0,
    ):
        page_size = max(1, int(getattr(ingest_service, "batch_size", 500) or 500))
        timeout = int(getattr(ingest_service, "request_timeout", 20) or 20)
        max_retries = int(getattr(ingest_service, "max_retries", 2) or 2)
        retry_delay = float(getattr(ingest_service, "retry_delay", 1) or 1)
        progress_interval = float(getattr(ingest_service, "progress_interval_s", 0.5) or 0.5)

        all_rows = []
        column_names = []
        last_progress_at = 0.0
        estimate = int(total_estimated_game_rows or 0)

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
            if len(all_rows) >= MAX_ROWS:
                break
            parsed = urlparse(endpoint)
            base_q = parse_qs(parsed.query)
            filtered = []
            for key, values in base_q.items():
                if key.lower() == "accesstype" and any(v.lower() == "download" for v in values):
                    continue
                for value in values:
                    filtered.append((key, value))

            print(f"[{str(game).upper()}] Endpoint {endpoint_idx + 1}/{len(endpoints)}: {endpoint}")
            offset = 0
            while len(all_rows) < MAX_ROWS:
                params = parse_qs(urlencode(filtered, doseq=True), keep_blank_values=True)
                params["$limit"] = [str(page_size)]
                params["$offset"] = [str(offset)]
                page_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

                page_ok = False
                data = None
                for attempt in range(max_retries):
                    try:
                        print(f"  Fetching offset={offset} attempt {attempt + 1}/{max_retries}")
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
                if extracted_columns and not column_names:
                    column_names = extracted_columns
                if not fetched_rows:
                    break

                remaining = MAX_ROWS - len(all_rows)
                if len(fetched_rows) > remaining:
                    fetched_rows = fetched_rows[:remaining]

                all_rows.extend(fetched_rows)
                emit(len(all_rows))
                time.sleep(0.05)

                if len(fetched_rows) > page_size * 2:
                    print(
                        f"  endpoint ignored $limit (got {len(fetched_rows)} rows); using single dump"
                    )
                    break
                if len(fetched_rows) < page_size:
                    break
                offset += len(fetched_rows)

            if len(all_rows) >= MAX_ROWS:
                print(f"  cap {MAX_ROWS} rows reached for {game}")
                break

        return all_rows, column_names

    ingest_service._fetch_all_rows_from_api = _fetch_all_rows_from_api
    print(f"Socrata fetch cap installed (max {MAX_ROWS} rows/game)")
