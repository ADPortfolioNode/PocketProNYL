"""
Unified Ingestion Manager - Industry-standard centralized ingestion system.
This consolidates startup and manual ingestion into a single, consistent workflow.
"""
import os
import threading
import time
import logging
from enum import Enum
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
import traceback

from config import GAME_CONFIGS
from services.ingest import ingest_service
from state.ingest_state import (
    startup_state,
    update_startup_state,
    set_game_status,
    get_startup_state,
    enqueue_manual_ingest,
    set_manual_ingest_state,
    get_manual_ingest_state,
)
from state.draw_counts import get_all_draw_counts, invalidate_draw_count
from state.manual_ingest_worker import _start_manual_ingest_worker_if_needed

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class IngestionMode(Enum):
    """Ingestion execution modes."""
    STARTUP = "startup"
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class IngestionStatus(Enum):
    """Standardized ingestion status values."""
    PENDING = "pending"
    QUEUED = "queued"
    FETCHING = "fetching"
    INGESTING = "ingesting"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class IngestionConfig:
    """Configuration for ingestion operations."""
    mode: IngestionMode = IngestionMode.STARTUP
    force: bool = False
    parallel_workers: int = 1
    batch_size: int = 500
    max_retries: int = 3
    retry_delay: float = 1.0
    timeout: float = 60.0
    skip_existing: bool = True
    validation_enabled: bool = True


@dataclass
class IngestionJob:
    """Represents a single ingestion job."""
    game: str
    config: IngestionConfig
    sequence: int = 0
    status: IngestionStatus = IngestionStatus.PENDING
    error: Optional[str] = None
    rows_fetched: int = 0
    total_rows: int = 0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class IngestionManager:
    """
    Unified ingestion manager following industry standards.
    Provides consistent, centralized ingestion control for all modes.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._active_jobs: Dict[str, IngestionJob] = {}
        self._job_queue: List[IngestionJob] = []
        self._worker_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._config: IngestionConfig = IngestionConfig()
        
        # Load configuration from environment
        self._load_config_from_env()
    
    def _load_config_from_env(self):
        """Load configuration from environment variables with fallbacks."""
        self._config = IngestionConfig(
            parallel_workers=max(1, int(os.getenv("INGEST_STARTUP_PARALLEL", "1"))),
            batch_size=int(os.getenv("INGEST_STARTUP_BATCH_SIZE", "500")),
            max_retries=int(os.getenv("INGEST_MAX_RETRIES", "3")),
            retry_delay=float(os.getenv("INGEST_RETRY_DELAY", "1.0")),
            timeout=float(os.getenv("INGEST_REQUEST_TIMEOUT", "60.0")),
            skip_existing=os.getenv("INGEST_SKIP_EXISTING", "1") == "1",
            validation_enabled=os.getenv("INGEST_VALIDATION_ENABLED", "1") == "1",
        )
        logger.info(f"Loaded ingestion config: {self._config}")
    
    def with_retry(self, max_retries: Optional[int] = None):
        """
        Decorator for retry logic with exponential backoff.
        Industry-standard pattern for resilient operations.
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                retries = max_retries or self._config.max_retries
                for attempt in range(retries):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        if attempt == retries - 1:
                            logger.error(f"Operation failed after {retries} attempts: {e}")
                            raise
                        delay = self._config.retry_delay * (2 ** attempt)
                        logger.warning(f"Attempt {attempt + 1}/{retries} failed, retrying in {delay}s: {e}")
                        time.sleep(delay)
            return wrapper
        return decorator
    
    def _prefetch_existing_counts(self, games: List[str]) -> Dict[str, int]:
        """Prefetch existing draw counts to avoid slow Chroma calls."""
        try:
            counts = get_all_draw_counts(games)
            for game in games:
                counts.setdefault(game, 0)
            logger.info(f"Prefetched existing counts for {len(games)} games")
            return counts
        except Exception as e:
            logger.error(f"Failed to prefetch counts: {e}")
            return {game: 0 for game in games}
    
    def _validate_ingestion_prerequisites(self, game: str) -> tuple[bool, str]:
        """Validate prerequisites before starting ingestion."""
        if game not in GAME_CONFIGS:
            return False, f"Unknown game: {game}"
        
        game_config = GAME_CONFIGS.get(game, {})
        if not game_config:
            return False, f"No configuration found for game: {game}"
        
        return True, ""
    
    def _update_job_status(self, job: IngestionJob, status: IngestionStatus, 
                          error: Optional[str] = None, **metadata):
        """Update job status and propagate to state systems."""
        job.status = status
        if error:
            job.error = error
        
        for key, value in metadata.items():
            job.metadata[key] = value
        
        # Update startup state if in startup mode
        if self._config.mode == IngestionMode.STARTUP:
            with self._lock:
                set_game_status(job.game, status.value, error=error)
                update_startup_state(
                    startup_state,
                    current_game=job.game,
                    current_task=status.value,
                    current_game_rows_fetched=job.rows_fetched,
                    current_game_rows_total=job.total_rows,
                    **metadata
                )
        
        # Update manual state if in manual mode
        if self._config.mode == IngestionMode.MANUAL:
            with self._lock:
                set_manual_ingest_state(job.game, {
                    "status": status.value,
                    "rows_fetched": job.rows_fetched,
                    "total_rows": job.total_rows,
                    "error": error,
                    **metadata
                })
    
    def _execute_ingestion_job(self, job: IngestionJob):
        """Execute a single ingestion job with comprehensive error handling."""
        job.started_at = time.time()
        job.status = IngestionStatus.INGESTING
        self._update_job_status(job, IngestionStatus.INGESTING)
        
        logger.info(f"Starting ingestion for {job.game} (mode: {self._config.mode.value})")
        
        try:
            # Validate prerequisites
            is_valid, error_msg = self._validate_ingestion_prerequisites(job.game)
            if not is_valid:
                raise ValueError(error_msg)
            
            # Progress callback
            def progress_callback(rows_fetched: int, total_rows: int):
                job.rows_fetched = rows_fetched
                job.total_rows = total_rows
                self._update_job_status(
                    job, 
                    IngestionStatus.INGESTING,
                    rows_fetched=rows_fetched,
                    total_rows=total_rows
                )
            
            # Execute ingestion with retry logic
            @self.with_retry()
            def fetch_with_retry():
                return ingest_service.fetch_and_sync(
                    job.game,
                    progress_callback=progress_callback,
                    force=job.config.force,
                )
            
            result = fetch_with_retry()
            
            # Process result
            if isinstance(result, dict):
                job.total_rows = result.get("total", job.rows_fetched)
                job.rows_fetched = result.get("total", job.rows_fetched)
            elif isinstance(result, list):
                job.total_rows = len(result)
                job.rows_fetched = len(result)
            
            job.completed_at = time.time()
            job.status = IngestionStatus.COMPLETED
            self._update_job_status(job, IngestionStatus.COMPLETED)
            
            logger.info(f"Successfully completed ingestion for {job.game}: {job.rows_fetched} rows")
            
        except Exception as e:
            job.completed_at = time.time()
            job.status = IngestionStatus.ERROR
            job.error = str(e)
            self._update_job_status(job, IngestionStatus.ERROR, error=str(e))
            logger.error(f"Failed to ingest {job.game}: {e}")
            logger.debug(traceback.format_exc())
    
    def _process_job_queue(self):
        """Process jobs from the queue with parallel execution support."""
        logger.info(f"Starting job queue processing with {self._config.parallel_workers} workers")
        
        while self._is_running and self._job_queue:
            # Get batch of jobs for parallel processing
            batch_size = min(self._config.parallel_workers, len(self._job_queue))
            current_batch = self._job_queue[:batch_size]
            self._job_queue = self._job_queue[batch_size:]
            
            if self._config.parallel_workers <= 1:
                # Sequential processing
                for job in current_batch:
                    if not self._is_running:
                        break
                    self._execute_ingestion_job(job)
            else:
                # Parallel processing
                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    futures = {
                        executor.submit(self._execute_ingestion_job, job): job
                        for job in current_batch
                    }
                    for future in as_completed(futures):
                        if not self._is_running:
                            break
                        try:
                            future.result()
                        except Exception as e:
                            logger.error(f"Job execution failed: {e}")
        
        logger.info("Job queue processing completed")
    
    def start_ingestion(self, games: Optional[List[str]] = None, 
                      mode: IngestionMode = IngestionMode.STARTUP,
                      force: bool = False) -> Dict[str, Any]:
        """
        Start ingestion for specified games or all configured games.
        Industry-standard interface with comprehensive status reporting.
        """
        if games is None:
            games = list(GAME_CONFIGS.keys())
        
        self._config.mode = mode
        self._config.force = force
        
        logger.info(f"Starting {mode.value} ingestion for {len(games)} games (force={force})")
        
        # Prefetch existing counts for optimization
        existing_counts = self._prefetch_existing_counts(games)
        
        # Create jobs
        jobs = []
        for game in games:
            existing_count = existing_counts.get(game, 0)
            
            # Skip if not forced and data exists
            if not force and existing_count > 0 and self._config.skip_existing:
                logger.info(f"Skipping {game} - {existing_count} rows already exist")
                job = IngestionJob(
                    game=game,
                    config=self._config,
                    status=IngestionStatus.SKIPPED,
                    rows_fetched=existing_count,
                    total_rows=existing_count,
                )
                self._update_job_status(job, IngestionStatus.SKIPPED)
                jobs.append(job)
                continue
            
            # Create ingestion job
            job = IngestionJob(
                game=game,
                config=self._config,
                sequence=len(jobs) + 1,
            )
            jobs.append(job)
            self._job_queue.append(job)
            self._active_jobs[game] = job
            
            # Enqueue in manual system for compatibility
            if mode == IngestionMode.MANUAL:
                seq = enqueue_manual_ingest(game, force=force)
                set_manual_ingest_state(game, {
                    "status": "queued",
                    "seq": seq,
                })
        
        # Start worker thread if needed
        if self._job_queue and not self._is_running:
            self._is_running = True
            self._worker_thread = threading.Thread(
                target=self._process_job_queue,
                daemon=True,
                name=f"IngestionWorker-{mode.value}"
            )
            self._worker_thread.start()
        
        return {
            "status": "started",
            "mode": mode.value,
            "total_games": len(games),
            "queued_jobs": len(self._job_queue),
            "skipped_jobs": len([j for j in jobs if j.status == IngestionStatus.SKIPPED]),
            "games": [j.game for j in jobs],
        }
    
    def get_ingestion_status(self) -> Dict[str, Any]:
        """Get comprehensive ingestion status across all jobs."""
        all_games = list(GAME_CONFIGS.keys())
        job_statuses = {}
        
        for game in all_games:
            job = self._active_jobs.get(game)
            if job:
                job_statuses[game] = {
                    "status": job.status.value,
                    "rows_fetched": job.rows_fetched,
                    "total_rows": job.total_rows,
                    "error": job.error,
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                    "metadata": job.metadata,
                }
            else:
                # Fallback to existing state systems
                startup = get_startup_state() or {}
                startup_games = startup.get("games", {})
                manual = get_manual_ingest_state(game) or {}
                
                # Merge statuses with priority to most recent
                status = manual.get("status") or startup_games.get(game, {}).get("status") or "pending"
                job_statuses[game] = {
                    "status": status,
                    "rows_fetched": manual.get("rows_fetched") or startup_games.get(game, {}).get("rows_fetched", 0),
                    "total_rows": manual.get("total_rows") or startup_games.get(game, {}).get("total_rows", 0),
                    "error": manual.get("error") or startup_games.get(game, {}).get("error"),
                }
        
        return {
            "is_running": self._is_running,
            "queue_length": len(self._job_queue),
            "active_jobs": len(self._active_jobs),
            "config": {
                "mode": self._config.mode.value,
                "parallel_workers": self._config.parallel_workers,
                "force": self._config.force,
            },
            "games": job_statuses,
        }
    
    def stop_ingestion(self):
        """Stop all ongoing ingestion operations gracefully."""
        logger.info("Stopping ingestion operations...")
        self._is_running = False
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=30)
        
        self._job_queue.clear()
        logger.info("Ingestion operations stopped")


# Global singleton instance
ingestion_manager = IngestionManager()