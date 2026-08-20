import requests
import hashlib
import time
import os
import re
import signal
from functools import wraps
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
import json # Added for json.dumps
from typing import Any

class TimeoutError(Exception):
    """Raised when an operation exceeds the time limit."""
    pass

def timeout_handler(signum, frame):
    raise TimeoutError("Operation timed out")

def with_timeout(seconds):
    """Decorator to add a timeout to a function (Unix/Linux only, will pass on Windows)."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # On Windows, signal.SIGALRM is not available; just run normally
            if not hasattr(signal, 'SIGALRM'):
                return func(*args, **kwargs)
            
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(seconds)
            try:
                result = func(*args, **kwargs)
            finally:
                signal.alarm(0)  # Cancel alarm
                signal.signal(signal.SIGALRM, old_handler)
            return result
        return wrapper
    return decorator


from fastapi import HTTPException
from config import GAME_CONFIGS, DATASET_ENDPOINTS, GAME_TITLES, GAME_ALIASES, resolve_game_key
from routes.chroma_repository import chroma_repository # Import the repository

from utils.game_data_parser import (
    _parse_pick3_digits as parse_pick3_digits_util,
    _extract_rows_and_columns as extract_rows_and_columns_util,
    _get_rules as get_game_rules,
    _clamp_primary as clamp_primary_util,
    _extract_bonus_values as extract_bonus_values_util,
    _extract_primary_candidate as extract_primary_candidate_util,
    _extract_record_sequence as extract_record_sequence_util,
)

class IngestService:
    def __init__(self):
        self.request_timeout = int(os.getenv("INGEST_REQUEST_TIMEOUT", "20"))
        self.max_retries = int(os.getenv("INGEST_MAX_RETRIES", "2"))
        self.retry_delay = int(os.getenv("INGEST_RETRY_DELAY", "1"))
        
        # Determine the effective batch size based on startup configuration
        # If INGEST_STARTUP_PARALLEL is enabled, use INGEST_STARTUP_BATCH_SIZE for Socrata API calls
        # Otherwise, use the general INGEST_BATCH_SIZE
        self.batch_size = int(os.getenv("INGEST_STARTUP_BATCH_SIZE", "500")) if os.getenv("INGEST_STARTUP_PARALLEL", "0") == "1" else int(os.getenv("INGEST_BATCH_SIZE", "4000"))
        self.use_upsert = os.getenv("INGEST_USE_UPSERT", "1") != "0"
        self.enable_catalog_fallback = os.getenv("INGEST_ENABLE_CATALOG_FALLBACK", "1") == "1"
        self.fallback_on_empty = os.getenv("INGEST_FALLBACK_ON_EMPTY", "1") == "1"
        self.max_catalog_candidates = int(os.getenv("INGEST_MAX_CATALOG_CANDIDATES", "3")) # PocketPro:NYL Project
        self.max_id_preload = int(os.getenv("INGEST_MAX_ID_PRELOAD", "5000")) # Lowered from 15000 to prevent OOM on large collections
        self.skip_fetch_threshold = int(os.getenv("INGEST_SKIP_FETCH_THRESHOLD", "50000"))
        self.batch_max_retries = int(os.getenv("INGEST_BATCH_MAX_RETRIES", "2"))
        self.progress_interval_s = float(os.getenv("INGEST_PROGRESS_INTERVAL_S", "0.5"))
        self.socrata_catalog_url = os.getenv("SOCRATA_CATALOG_URL", "https://api.us.socrata.com/api/catalog/v1")
        self.socrata_domain = os.getenv("SOCRATA_DOMAIN", "data.ny.gov")
        self.game_search_hints = {
            key: f"new york {GAME_TITLES.get(key, key)} lottery"
            for key in DATASET_ENDPOINTS.keys()
        }

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    def _fetch_catalog_endpoints(self, game: str):
        """Discover fallback dataset endpoints from Socrata catalog for a game."""
        if not self.enable_catalog_fallback:
            return []

        hint = self.game_search_hints.get(game, GAME_TITLES.get(game, game))
        query = hint

        try:
            response = requests.get(
                self.socrata_catalog_url,
                params={
                    "domains": self.socrata_domain,
                    "search_context": self.socrata_domain,
                    "q": query,
                    "limit": 20,
                },
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results", [])
        except Exception as exc:
            print(f"⚠ Catalog lookup failed for {game}: {exc}")
            return []

        game_tokens = set(self._normalize_text(GAME_TITLES.get(game, game)).split())
        alias_tokens = {
            token
            for alias in GAME_ALIASES.get(game, [])
            for token in self._normalize_text(alias).split()
        }
        all_expected_tokens = {token for token in (game_tokens | alias_tokens) if token and token != "lottery"}
        ranked = []

        for item in results:
            resource = item.get("resource") or {}
            dataset_id = resource.get("id")
            if not dataset_id:
                continue

            name = self._normalize_text(resource.get("name", ""))
            description = self._normalize_text(resource.get("description", ""))
            combined = f"{name} {description}"

            score = 0
            if "lottery" in combined:
                score += 3
            token_hits = sum(1 for token in all_expected_tokens if token in combined)
            if token_hits > 0:
                score += min(token_hits, 4)
            compact_expected = self._normalize_text(GAME_TITLES.get(game, game)).replace(" ", "")
            if compact_expected and compact_expected in combined.replace(" ", ""):
                score += 5
            if "new york" in combined:
                score += 2

            # Skip unrelated catalog hits
            if score <= 0:
                continue

            endpoint = f"https://{self.socrata_domain}/api/views/{dataset_id}/rows.json?accessType=DOWNLOAD"
            ranked.append((score, endpoint, resource.get("name", dataset_id)))

        ranked.sort(key=lambda x: x[0], reverse=True)

        deduped_endpoints = []
        seen = set()
        for _, endpoint, title in ranked:
            if endpoint in seen:
                continue
            seen.add(endpoint)
            deduped_endpoints.append(endpoint)
            print(f"  ↳ Catalog candidate for {game}: {title} -> {endpoint}")

            if len(deduped_endpoints) >= self.max_catalog_candidates:
                break

        return deduped_endpoints

    def _resolve_game_endpoints(self, game: str):
        configured = DATASET_ENDPOINTS.get(game, [])

        # Fast path: use known configured datasets by default.
        # Catalog fallback is opt-in and primarily for recovery when configured endpoints are missing.
        if configured and not self.enable_catalog_fallback:
            return configured

        catalog_candidates = self._fetch_catalog_endpoints(game)

        if configured and not catalog_candidates:
            return configured

        ordered = []
        seen = set()
        for endpoint in configured + catalog_candidates:
            if endpoint in seen:
                continue
            seen.add(endpoint)
            ordered.append(endpoint)

        return ordered

    def _pick3_digit_string(self, raw_value) -> str | None:
        # Use the centralized pick3 parser from utils to get digits, then join to string
        parsed_digits = parse_pick3_digits_util(raw_value)
        return "".join(str(d) for d in parsed_digits) if len(parsed_digits) == 3 else None

    def _process_api_row(self, row_dict: dict, game: str, column_names: list[str], existing_ids: set[str]) -> list[tuple[str, dict, str]]:
        """
        Process a single row from the API, normalize it, and prepare for ChromaDB.
        This method was restored to fix a regression where it was missing.
        """
        metadata_item = row_dict
        
        # Generate a base ID for records that don't get expanded
        draw_date = metadata_item.get("draw_date")
        winning_numbers = metadata_item.get("winning_numbers")
        row_id_base = hashlib.md5(f"{game}|{draw_date}|{str(winning_numbers)}".encode()).hexdigest()

        # Normalize the record, which may expand it into multiple records (e.g., for Pick 3)
        normalized_records = self._normalize_game_records(game, metadata_item, row_id_base)
        
        records_to_add = []
        for record_id, record_meta in normalized_records:
            if record_id in existing_ids:
                continue
            doc_text = json.dumps(record_meta, sort_keys=True)
            records_to_add.append((record_id, record_meta, doc_text))
        return records_to_add

    def _normalize_game_records(self, game: str, metadata_item: dict, row_id_base: str) -> list[tuple[str, dict]]:
        """
        Expand parent records into child records (e.g., for pick3) based on game configuration
        and normalize field values like lists into storable string formats.
        This acts as a "spider" for nested data within a single API record.
        """
        records_to_process: list[tuple[str, dict]]
        
        game_config = GAME_CONFIGS.get(game, {})
        expansion_config = game_config.get("expansion")

        # Data-driven expansion logic to handle parent-child records within a single API row.
        # This acts as a "spider" for nested data structures, as seen with Pick 3's midday/evening draws.
        if expansion_config and expansion_config.get("type") == "field_to_record":
            draw_date = str(metadata_item.get("draw_date") or "").strip()
            expanded: list[tuple[str, dict]] = []
            
            fields_to_expand = expansion_config.get("fields", {})
            
            for source_field, child_meta in fields_to_expand.items():
                parser_name = expansion_config.get("parser")
                raw_value = metadata_item.get(source_field)
                
                if parser_name == "pick3_digits":
                    value_for_record = self._pick3_digit_string(raw_value)
                else: # Default parser just stringifies the value
                    value_for_record = str(raw_value or "").strip()

                if not value_for_record:
                    continue
                
                child_record: dict[str, Any] = metadata_item.copy()
                child_record.update(child_meta) # Add fields like {"draw_session": "midday"}
                
                value_field = expansion_config.get("value_field", "winning_numbers")
                child_record[value_field] = value_for_record
                
                # Ensure draw_date is consistent
                child_record["draw_date"] = draw_date or str(metadata_item.get("draw_date") or "")
                
                # Build a unique ID for the child record from configured fields
                id_parts = [game]
                for id_field in expansion_config.get("id_fields", ["draw_date", value_field]):
                    id_parts.append(str(child_record.get(id_field, "")))
                
                row_id = hashlib.md5("|".join(id_parts).encode()).hexdigest()
                expanded.append((row_id, child_record))

            records_to_process = expanded if expanded else [(row_id_base, metadata_item)]
        else:
            # Original behavior for games without expansion config
            records_to_process = [(row_id_base, metadata_item)]

        final_records: list[tuple[str, dict]] = []
        for r_id, rec in records_to_process:
            normalized_rec = rec.copy()
            # Standardize 'winning_numbers' to a comma-separated string if it's a space-separated string.
            # This ensures consistent parsing by downstream services (e.g., predictor).
            if 'winning_numbers' in normalized_rec and isinstance(normalized_rec['winning_numbers'], str):
                if ' ' in normalized_rec['winning_numbers'] and ',' not in normalized_rec['winning_numbers']:
                    normalized_rec['winning_numbers'] = normalized_rec['winning_numbers'].replace(' ', ',')

            for key, value in normalized_rec.items():
                if isinstance(value, list):
                    normalized_rec[key] = ",".join(map(str, value))
            
            final_records.append((r_id, normalized_rec))

        return final_records
    
    def _fetch_all_rows_from_api(
        self,
        game: str,
        endpoints: list[str],
        progress_callback=None,
        total_estimated_game_rows: int = 0,
    ) -> tuple[list[dict], list[str]]:
        """
        Fetches all rows from a list of paginated API endpoints.
        Returns a tuple of (all_rows, column_names).
        """
        socrata_page_size = self.batch_size # Use batch_size as page size for Socrata API
        all_fetched_rows = []
        _current_column_names: list[str] = []
        last_progress_at = 0.0

        def _emit_progress(rows_fetched: int, total_rows: int):
            nonlocal last_progress_at
            if not progress_callback:
                return
            now = time.time()
            if (rows_fetched >= total_rows or now - last_progress_at >= self.progress_interval_s):
                progress_callback(rows_fetched, total_rows)
                last_progress_at = now

        for endpoint_idx, endpoint in enumerate(endpoints):
            endpoint_last_error = None
            parsed_url = urlparse(endpoint)
            base_query_params = parse_qs(parsed_url.query)
            filtered_query_items = []
            for key, values in base_query_params.items():
                if key.lower() == 'accesstype' and any(v.lower() == 'download' for v in values):
                    continue
                for value in values:
                    filtered_query_items.append((key, value))

            print(f"[{game.upper()}] Endpoint {endpoint_idx + 1}/{len(endpoints)}: {endpoint}")

            current_offset = 0
            while True:
                paginated_query_params = parse_qs(urlencode(filtered_query_items, doseq=True), keep_blank_values=True)
                paginated_query_params['$limit'] = [socrata_page_size]
                paginated_query_params['$offset'] = [current_offset]
                new_query = urlencode(paginated_query_params, doseq=True)
                paginated_endpoint = urlunparse(parsed_url._replace(query=new_query))
                
                for attempt in range(self.max_retries):
                    page_success: bool = False
                    response = None
                    data = None
                    try:
                        print(f"  Fetching from {paginated_endpoint} (attempt {attempt + 1}/{self.max_retries})...")
                        response = requests.get(paginated_endpoint, timeout=self.request_timeout)
                        response.raise_for_status()
                        data = response.json()
                        page_success = True
                        break
                    except requests.Timeout:
                        endpoint_last_error = f"Request timeout after {self.request_timeout}s for {paginated_endpoint}"
                        print(f"  ⚠ {endpoint_last_error}")
                        if attempt < self.max_retries - 1:
                            time.sleep(self.retry_delay * (2 ** attempt))
                        else:
                            break
                    except requests.RequestException as req_error:
                        endpoint_last_error = f"Request error: {str(req_error)} for {paginated_endpoint}"
                        print(f"  ⚠ {endpoint_last_error}")
                        if hasattr(req_error, 'response') and req_error.response is not None:
                            print(f"  Response status: {req_error.response.status_code}")
                            print(f"  Response preview: {req_error.response.text[:200]}")
                        if attempt < self.max_retries - 1:
                            time.sleep(self.retry_delay * (2 ** attempt))
                            continue
                        else:
                            break
                    except ValueError as json_error:
                        endpoint_last_error = f"Invalid JSON response: {str(json_error)} for {paginated_endpoint}"
                        print(f"  ⚠ {endpoint_last_error}")
                        if response:
                            print(f"  Response preview: {response.text[:200]}") 
                        break
                    except Exception as unexpected_error:
                        endpoint_last_error = f"Unexpected error: {str(unexpected_error)} for {paginated_endpoint}"
                        print(f"  ✗ {endpoint_last_error}")
                        import traceback
                        traceback.print_exc()
                        break

                if not page_success:
                    print(f"  ✗ Failed to fetch page at offset {current_offset} from {endpoint}: {endpoint_last_error}")
                    break

                fetched_rows, extracted_columns = extract_rows_and_columns_util(data)

                if not _current_column_names and extracted_columns:
                    _current_column_names = extracted_columns
                    print(f"  ✓ Extracted {len(_current_column_names)} column names from payload")

                if not fetched_rows:
                    print(f"  ✓ No more data from {paginated_endpoint} (offset {current_offset})")
                    break
                
                all_fetched_rows.extend(fetched_rows)
                _emit_progress(len(all_fetched_rows), total_estimated_game_rows)
                current_offset += len(fetched_rows)

        return all_fetched_rows, _current_column_names

    def _process_rows_from_file(
        self,
        game: str,
        all_rows: list[dict],
        collection,
        existing_ids: set[str],
        column_names: list[str],
        progress_callback=None,
    ) -> tuple[int, int]:
        """Processes a list of rows from a file and upserts them into ChromaDB."""
        chroma_batch_size = self.batch_size
        total_rows_processed_in_run = 0
        rows_added = 0
        last_progress_at = 0.0

        def _emit_progress(processed: int, total: int):
            nonlocal last_progress_at
            if not progress_callback:
                return
            now = time.time()
            if (processed >= total or now - last_progress_at >= self.progress_interval_s):
                progress_callback(processed, total)
                last_progress_at = now

        for j in range(0, len(all_rows), chroma_batch_size):
            chroma_batch = all_rows[j:j + chroma_batch_size]
            chroma_metadatas, chroma_ids, chroma_documents = [], [], []

            for row in chroma_batch:
                processed_records = self._process_api_row(row, game, column_names, existing_ids)
                for record_id, record_meta, doc_text in processed_records:
                    chroma_ids.append(record_id)
                    chroma_metadatas.append(record_meta)
                    chroma_documents.append(doc_text)

            if not chroma_ids:
                continue

            batch_stored = False
            for batch_attempt in range(self.batch_max_retries):
                try:
                    collection.upsert(documents=chroma_documents, metadatas=chroma_metadatas, ids=chroma_ids)
                    existing_ids.update(chroma_ids)
                    rows_added += len(chroma_ids)
                    batch_stored = True
                    break
                except Exception as batch_error:
                    if batch_attempt < self.batch_max_retries - 1:
                        delay = self.retry_delay * (2 ** batch_attempt)
                        print(f"  ⚠ ChromaDB batch failed (attempt {batch_attempt + 1}): {batch_error}; retrying in {delay:.1f}s")
                        time.sleep(delay)

            if not batch_stored:
                print(f"  ✗ Error processing batch after {self.batch_max_retries} attempts. Skipping.")
                continue

            total_rows_processed_in_run += len(chroma_ids)
            _emit_progress(total_rows_processed_in_run, len(all_rows))

        return total_rows_processed_in_run, rows_added

    def _build_success_result(
        self,
        *,
        existing_count: int,
        total_rows_processed: int,
        total_rows_added: int,
        final_total: int,
        force: bool,
        skipped_fetch: bool = False,
    ) -> dict:
        net_added = max(final_total - existing_count, 0)
        return {
            "status": "success",
            "added": net_added,
            "total": final_total,
            "processed": total_rows_processed,
            "skipped_existing": skipped_fetch,
            "incremental": bool(existing_count > 0 and not force),
            "added_in_run": total_rows_added,
            "skipped_fetch": skipped_fetch,
        }

    def fetch_and_sync(self, game: str, progress_callback=None, force: bool = False) -> dict:
        """
        Fetch game data and sync to ChromaDB. This implementation first downloads
        all data to a local JSON file for robustness, then processes from that file.
        
        Args:
            game: Game name
            progress_callback: Optional callback function(rows_fetched, total_rows) for progress tracking
            force: If True, bypasses caches and re-fetches all data.
        """
        game_key = resolve_game_key(game)
        if not game_key:
            raise HTTPException(status_code=400, detail=
                f"Unknown game '{game}'. Available configured games: {list(DATASET_ENDPOINTS.keys())}"
            )

        collection_name = game_key
        try:
            collection = chroma_repository.get_or_create_collection(collection_name)
            print(f"✓ Connected to collection '{collection_name}'")
        except Exception as conn_error:
            raise Exception(f"Failed to connect to ChromaDB collection '{collection_name}': {str(conn_error)}")

        # --- JSON Caching and Fetching Logic ---
        json_cache_dir = "/data/ingestion_cache"
        os.makedirs(json_cache_dir, exist_ok=True)
        json_path = os.path.join(json_cache_dir, f"{game_key}.json")

        if force and os.path.exists(json_path):
            os.remove(json_path)
            print(f"  ✓ Removed cached JSON file for forced re-ingestion.")
            try:
                from state.draw_counts import invalidate_draw_count
                invalidate_draw_count(collection_name)
            except Exception:
                pass

        all_rows = []
        column_names = []

        if not os.path.exists(json_path):
            print(f"  ↳ No cache found for {game_key}. Fetching from API...")
            endpoints = self._resolve_game_endpoints(game_key)
            if not endpoints:
                raise ValueError(f"No endpoints found for game '{game_key}'.")

            total_estimated_game_rows = 0
            for ep in endpoints:
                try:
                    parsed_url = urlparse(ep)
                    query_params = parse_qs(parsed_url.query)
                    query_params.pop('accessType', None)
                    query_params['$select'] = ['count(*)']
                    new_query = urlencode(query_params, doseq=True)
                    count_endpoint = urlunparse(parsed_url._replace(query=new_query))
                    count_response = requests.get(count_endpoint, timeout=15)
                    count_response.raise_for_status()
                    count_data = count_response.json()
                    if count_data and isinstance(count_data, list) and len(count_data) > 0:
                        count_for_ep = int(count_data[0].get('count', 0))
                        total_estimated_game_rows += count_for_ep
                except Exception as e:
                    print(f"  ⚠ Could not get total row count for an endpoint: {e}.")
            
            if total_estimated_game_rows == 0:
                print(f"  ⚠ Using fallback total of 100000 for progress reporting.")
                total_estimated_game_rows = 100000

            if progress_callback:
                progress_callback(0, total_estimated_game_rows)

            all_rows, column_names = self._fetch_all_rows_from_api(
                game=game_key,
                endpoints=endpoints,
                progress_callback=progress_callback,
                total_estimated_game_rows=total_estimated_game_rows
            )

            if all_rows:
                try:
                    with open(json_path, "w") as f:
                        json.dump({"rows": all_rows, "columns": column_names}, f)
                    print(f"  ✓ Saved {len(all_rows)} rows to cache at {json_path}")
                except Exception as e:
                    print(f"  ⚠ Failed to save cache file: {e}")

        # --- Processing from Cache or Loaded Data ---
        if not all_rows and os.path.exists(json_path):
            print(f"  ✓ Loading from cached JSON file: {json_path}")
            with open(json_path, "r") as f:
                cached_data = json.load(f)
            all_rows = cached_data.get("rows", [])
            column_names = cached_data.get("columns", [])
            if progress_callback:
                progress_callback(len(all_rows), len(all_rows))

        if not all_rows:
            raise Exception(f"No data was successfully ingested for game '{game_key}'. Check backend logs for details.")

        # --- Deduplication and Upsert Logic ---
        existing_count = 0
        existing_ids: set[str] = set()
        if not force:
            existing_count = chroma_repository.count_documents(collection_name)
            if existing_count > 0:
                print(f"↻ [{game_key.upper()}] Existing records detected ({existing_count}). Incremental sync will apply.")
                if existing_count <= self.max_id_preload:
                    try:
                        existing_payload = collection.get(include=[])
                        existing_ids = set(existing_payload.get("ids") or [])
                        print(f"  ✓ Loaded {len(existing_ids)} existing IDs for dedupe")
                    except Exception as e:
                        print(f"  ⚠ Could not pre-load existing IDs ({e}); falling back to upsert-only sync")
                else:
                    print(f"  ↳ Skipping ID preload ({existing_count} > {self.max_id_preload}); upsert dedupe only")

        total_rows_processed, total_rows_added = self._process_rows_from_file(
            game=game_key,
            all_rows=all_rows,
            collection=collection,
            existing_ids=existing_ids,
            column_names=column_names,
            progress_callback=progress_callback,
        )

        # --- Finalization ---
        final_total = max(existing_count + total_rows_added, existing_count, total_rows_processed)
        try:
            from state.draw_counts import update_draw_count
            update_draw_count(collection_name, final_total)
        except Exception:
            pass
        if progress_callback:
            progress_callback(final_total, final_total)

        net_added = max(final_total - existing_count, 0)
        
        print(
            f"✓ [{game_key.upper()}] Ingestion complete: processed={total_rows_processed}, "
            f"added_in_run={total_rows_added}, net_added={net_added}, total={final_total}"
        )
        if total_rows_added > 0 and os.environ.get("PREDICTION_ENGINE", "modular").lower() != "legacy":
            try:
                latest = chroma_repository.get_documents(collection_name, limit=1, include=["metadatas", "ids"])
                metas = latest.get("metadatas") or []
                ids = latest.get("ids") or []
                if metas:
                    from prediction.adapter_hooks import on_new_draw_metadata
                    on_new_draw_metadata(game_key, metas[0], ids[0] if ids else None)
            except Exception as hook_error:
                print(f"⚠ [{game_key.upper()}] Weight update hook skipped: {hook_error}")

        return self._build_success_result(
            existing_count=existing_count,
            total_rows_processed=total_rows_processed,
            total_rows_added=total_rows_added,
            final_total=final_total,
            force=force,
        )

ingest_service = IngestService()
