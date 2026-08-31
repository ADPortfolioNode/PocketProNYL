"""
Unified Health Monitoring System - Industry-standard health checks.
Provides comprehensive health monitoring for all services with dependency tracking.
"""
import time
import logging
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from abc import ABC, abstractmethod
from functools import wraps

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Standardized health status values."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check operation."""
    status: HealthStatus
    message: str
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "status": self.status.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


class HealthCheck(ABC):
    """Abstract base class for health checks."""
    
    def __init__(self, name: str, timeout: float = 5.0):
        self.name = name
        self.timeout = timeout
        self._last_result: Optional[HealthCheckResult] = None
        self._last_check_time: float = 0
        self._cache_duration: float = 30.0  # Cache results for 30 seconds
    
    @abstractmethod
    def check(self) -> HealthCheckResult:
        """Perform the health check."""
        pass
    
    def get_cached_result(self) -> Optional[HealthCheckResult]:
        """Get cached result if still valid."""
        if self._last_result and (time.time() - self._last_check_time) < self._cache_duration:
            return self._last_result
        return None
    
    def check_with_cache(self, force: bool = False) -> HealthCheckResult:
        """Check health with caching support."""
        if not force:
            cached = self.get_cached_result()
            if cached:
                return cached
        
        start_time = time.time()
        try:
            result = self.check()
            result.duration_ms = (time.time() - start_time) * 1000
            self._last_result = result
            self._last_check_time = time.time()
            return result
        except Exception as e:
            logger.error(f"Health check failed for {self.name}: {e}")
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
                duration_ms=(time.time() - start_time) * 1000,
            )


class DatabaseHealthCheck(HealthCheck):
    """Health check for database connectivity."""
    
    def __init__(self, chroma_client, timeout: float = 5.0):
        super().__init__("database", timeout)
        self.chroma_client = chroma_client
    
    def check(self) -> HealthCheckResult:
        """Check database connectivity."""
        try:
            start_time = time.time()
            heartbeat = self.chroma_client.heartbeat()
            duration = (time.time() - start_time) * 1000
            
            if heartbeat.get("status") == "ok":
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Database is responsive",
                    duration_ms=duration,
                    metadata={"heartbeat": heartbeat},
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Database heartbeat failed: {heartbeat}",
                    duration_ms=duration,
                )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Database check failed: {str(e)}",
            )


class APIServiceHealthCheck(HealthCheck):
    """Health check for API service availability."""
    
    def __init__(self, base_url: str, timeout: float = 5.0):
        super().__init__("api_service", timeout)
        self.base_url = base_url
    
    def check(self) -> HealthCheckResult:
        """Check API service availability."""
        try:
            import requests
            start_time = time.time()
            response = requests.get(f"{self.base_url}/api/health", timeout=self.timeout)
            duration = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="API service is responding",
                    duration_ms=duration,
                    metadata={"status_code": response.status_code},
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"API service returned status {response.status_code}",
                    duration_ms=duration,
                    metadata={"status_code": response.status_code},
                )
        except requests.Timeout:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message="API service timeout",
            )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"API service check failed: {str(e)}",
            )


class IngestionHealthCheck(HealthCheck):
    """Health check for ingestion system status."""
    
    def __init__(self, ingestion_manager, timeout: float = 5.0):
        super().__init__("ingestion", timeout)
        self.ingestion_manager = ingestion_manager
    
    def check(self) -> HealthCheckResult:
        """Check ingestion system status."""
        try:
            status = self.ingestion_manager.get_ingestion_status()
            
            if status["is_running"]:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"Ingestion system is running ({status['queue_length']} jobs queued)",
                    metadata=status,
                )
            elif status["queue_length"] > 0:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Ingestion system has {status['queue_length']} queued jobs",
                    metadata=status,
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Ingestion system is idle",
                    metadata=status,
                )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Ingestion check failed: {str(e)}",
            )


class MemoryHealthCheck(HealthCheck):
    """Health check for system memory usage."""
    
    def __init__(self, threshold_percent: float = 90.0, timeout: float = 2.0):
        super().__init__("memory", timeout)
        self.threshold_percent = threshold_percent
    
    def check(self) -> HealthCheckResult:
        """Check memory usage."""
        try:
            import psutil
            memory = psutil.virtual_memory()
            percent_used = memory.percent
            
            if percent_used >= self.threshold_percent:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Memory usage critical: {percent_used:.1f}%",
                    metadata={
                        "percent_used": percent_used,
                        "available_gb": memory.available / (1024**3),
                        "total_gb": memory.total / (1024**3),
                    },
                )
            elif percent_used >= self.threshold_percent * 0.8:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Memory usage high: {percent_used:.1f}%",
                    metadata={
                        "percent_used": percent_used,
                        "available_gb": memory.available / (1024**3),
                    },
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"Memory usage normal: {percent_used:.1f}%",
                    metadata={
                        "percent_used": percent_used,
                        "available_gb": memory.available / (1024**3),
                    },
                )
        except ImportError:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="psutil not available for memory monitoring",
            )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Memory check failed: {str(e)}",
            )


class HealthMonitor:
    """
    Centralized health monitoring system.
    Manages multiple health checks and provides unified status reporting.
    """
    
    def __init__(self):
        self._checks: Dict[str, HealthCheck] = {}
        self._check_dependencies: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
    
    def register_check(self, check: HealthCheck, dependencies: Optional[List[str]] = None):
        """Register a health check with optional dependencies."""
        with self._lock:
            self._checks[check.name] = check
            if dependencies:
                self._check_dependencies[check.name] = dependencies
            logger.info(f"Registered health check: {check.name}")
    
    def unregister_check(self, name: str):
        """Unregister a health check."""
        with self._lock:
            if name in self._checks:
                del self._checks[name]
            if name in self._check_dependencies:
                del self._check_dependencies[name]
            logger.info(f"Unregistered health check: {name}")
    
    def check_service(self, name: str, force: bool = False) -> HealthCheckResult:
        """Check a specific service."""
        with self._lock:
            check = self._checks.get(name)
            if not check:
                return HealthCheckResult(
                    status=HealthStatus.UNKNOWN,
                    message=f"No health check registered for: {name}",
                )
            
            # Check dependencies first
            dependencies = self._check_dependencies.get(name, [])
            for dep in dependencies:
                dep_result = self.check_service(dep, force=force)
                if dep_result.status != HealthStatus.HEALTHY:
                    return HealthCheckResult(
                        status=HealthStatus.UNHEALTHY,
                        message=f"Dependency {dep} is unhealthy: {dep_result.message}",
                        metadata={"failed_dependency": dep, "dependency_result": dep_result.to_dict()},
                    )
            
            return check.check_with_cache(force=force)
    
    def check_all(self, force: bool = False) -> Dict[str, HealthCheckResult]:
        """Check all registered services."""
        with self._lock:
            results = {}
            for name in self._checks:
                results[name] = self.check_service(name, force=force)
            return results
    
    def get_overall_status(self, force: bool = False) -> HealthCheckResult:
        """Get overall system health status."""
        results = self.check_all(force=force)
        
        statuses = [result.status for result in results.values()]
        
        if all(status == HealthStatus.HEALTHY for status in statuses):
            overall_status = HealthStatus.HEALTHY
            message = "All systems healthy"
        elif any(status == HealthStatus.UNHEALTHY for status in statuses):
            overall_status = HealthStatus.UNHEALTHY
            unhealthy_services = [name for name, result in results.items() if result.status == HealthStatus.UNHEALTHY]
            message = f"Unhealthy services: {', '.join(unhealthy_services)}"
        else:
            overall_status = HealthStatus.DEGRADED
            degraded_services = [name for name, result in results.items() if result.status == HealthStatus.DEGRADED]
            message = f"Degraded services: {', '.join(degraded_services)}"
        
        return HealthCheckResult(
            status=overall_status,
            message=message,
            metadata={
                "service_count": len(results),
                "healthy_count": sum(1 for s in statuses if s == HealthStatus.HEALTHY),
                "degraded_count": sum(1 for s in statuses if s == HealthStatus.DEGRADED),
                "unhealthy_count": sum(1 for s in statuses if s == HealthStatus.UNHEALTHY),
                "service_results": {name: result.to_dict() for name, result in results.items()},
            },
        )


# Global singleton instance
health_monitor = HealthMonitor()