import threading
import time

import chromadb
import requests
from chromadb.config import Settings
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from config import settings
from state.draw_counts import get_all_draw_counts, get_draw_count, set_draw_count

CHROMA_REST_TIMEOUT = 4.0
CHROMA_COUNT_WORKERS = 1
CHROMA_REST_LOCK = threading.Lock()


class ChromaClient:
    def __init__(self):
        self._client = None
        self._collection_ids: dict[str, str] = {}
        self._collection_names: set[str] = set()
        self._catalog_loaded_at = 0.0

    @property
    def rest_base(self) -> str:
        return f"http://{settings.CHROMA_HOST}:{settings.CHROMA_PORT}"
 
    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.HttpClient(
                host=settings.CHROMA_HOST,
                port=settings.CHROMA_PORT,
                settings=Settings(anonymized_telemetry=False),
            )
        return self._client

    def _refresh_collection_catalog(self, timeout_seconds: float = CHROMA_REST_TIMEOUT, force: bool = False) -> None:
        now = time.time()
        if not force and self._collection_ids and (now - self._catalog_loaded_at) < 30:
            return

        with CHROMA_REST_LOCK:
            response = requests.get(
                f"{self.rest_base}/api/v1/collections",
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            ids = {
                item["name"]: item["id"]
                for item in payload
                if item.get("name") and item.get("id")
            }
            self._collection_ids = ids
            self._collection_names = set(ids.keys())
            self._catalog_loaded_at = time.time()

    def collection_exists(self, collection_name: str, timeout_seconds: float = CHROMA_REST_TIMEOUT) -> bool:
        try:
            self._refresh_collection_catalog(timeout_seconds=timeout_seconds)
            return collection_name in self._collection_names
        except Exception as exc:
            print(f"Error checking collection existence for {collection_name}: {exc}")
            return False

    def _rest_count(self, collection_name: str, timeout_seconds: float = CHROMA_REST_TIMEOUT) -> int:
        with CHROMA_REST_LOCK:
            collection_id = self._collection_ids.get(collection_name)
            if not collection_id:
                self._refresh_collection_catalog(timeout_seconds=timeout_seconds, force=True)
                collection_id = self._collection_ids.get(collection_name)
            if not collection_id:
                return 0

            response = requests.get(
                f"{self.rest_base}/api/v1/collections/{collection_id}/count",
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return int(payload.get("count") or 0)
            return int(payload or 0)

    def _sdk_count(self, collection_name: str) -> int:
        try:
            collection = self.client.get_collection(collection_name)
            return int(collection.count() or 0)
        except Exception as exc:
            print(f"Error counting documents via SDK for {collection_name}: {exc}")
            return 0

    def _live_count(self, collection_name: str, timeout_seconds: float = CHROMA_REST_TIMEOUT) -> int:
        try:
            return self._rest_count(collection_name, timeout_seconds=timeout_seconds)
        except Exception as exc:
            print(f"REST count failed for {collection_name}: {exc}")
            return self._sdk_count(collection_name)

    def get_chroma_status(self):
        try:
            self.client.heartbeat()
            return {"status": "ok"}
        except Exception as e:
            try:
                url = f"{self.rest_base}/api/v1/pre-flight-checks"
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    return {"status": "ok", "http_status": response.status_code}
                return {"status": "error", "message": f"http_status={response.status_code}", "body": response.text[:200]}
            except Exception as http_exc:
                return {"status": "error", "message": str(e), "http_error": str(http_exc)}

    def list_collections(self):
        return self.client.list_collections()

    def get_collection(self, collection_name: str):
        return self.client.get_collection(collection_name)

    def get_or_create_collection(self, collection_name: str):
        return self.client.get_or_create_collection(collection_name)

    def count_documents(
        self,
        collection_name: str,
        timeout_seconds: float = CHROMA_REST_TIMEOUT,
        *,
        allow_refresh: bool = True,
    ) -> int:
        cached = get_draw_count(collection_name, default=-1)
        if cached >= 0:
            return cached

        if not allow_refresh:
            return 0

        try:
            count = self._live_count(collection_name, timeout_seconds=timeout_seconds)
            if count > 0:
                set_draw_count(collection_name, count)
            return count
        except Exception as exc:
            print(f"Error counting documents for {collection_name}: {exc}")
            return get_draw_count(collection_name, default=0)

    def get_collections_snapshot(
        self,
        collection_names: list[str],
        timeout_seconds: float = 10.0,
        refresh: bool = False,
    ) -> list[dict]:
        cached_counts = get_all_draw_counts(collection_names)
        snapshots: list[dict] = []

        for idx, name in enumerate(collection_names, start=1):
            count = int(cached_counts.get(name, 0))
            state = "cached" if name in cached_counts else "unknown"
            snapshots.append(
                {
                    "name": name,
                    "record_index": idx,
                    "count": count,
                    "metadata": {},
                    "state": state,
                }
            )

        try:
            self._refresh_collection_catalog(timeout_seconds=min(timeout_seconds, CHROMA_REST_TIMEOUT))
        except Exception as exc:
            print(f"Error listing Chroma collections: {exc}")
            return snapshots

        for snapshot in snapshots:
            name = snapshot["name"]
            if name not in self._collection_names:
                if snapshot["state"] == "unknown":
                    snapshot["state"] = "empty"
                continue
            if snapshot["state"] == "unknown":
                snapshot["state"] = "exists"
            if refresh or (snapshot["state"] == "exists" and snapshot["count"] <= 0):
                live_count = self._live_count(name, timeout_seconds=min(timeout_seconds, CHROMA_REST_TIMEOUT))
                if live_count > 0:
                    snapshot["count"] = int(live_count)
                    snapshot["state"] = "refreshed"
                    set_draw_count(name, live_count)
                elif int(cached_counts.get(name, 0)) > 0:
                    snapshot["count"] = int(cached_counts[name])
                    snapshot["state"] = "cached"
                else:
                    snapshot["count"] = 0
                    snapshot["state"] = "refreshed"

        return snapshots

chroma_client = ChromaClient()