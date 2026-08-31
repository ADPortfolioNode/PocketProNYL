"""
Circuit Breaker Pattern Implementation
Industry-standard pattern for handling external API failures and preventing cascading failures.
"""
import time
import logging
from enum import Enum
from typing import Callable, Any, Optional
from functools import wraps
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit is open, blocking calls
    HALF_OPEN = "half_open"  # Testing if service has recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 5        # Failures before opening
    success_threshold: int = 2        # Successes to close circuit
    timeout: float = 60.0             # Seconds before trying again
    expected_exception: Exception = Exception  # Exception type to track
    name: str = "default"             # Circuit breaker name


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker monitoring."""
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    total_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreaker:
    """
    Circuit breaker implementation following industry standards.
    Prevents cascading failures by blocking calls to failing services.
    """
    
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.stats = CircuitBreakerStats()
        self._lock = Lock()
        self._last_state_change = time.time()
        
    def _should_attempt_call(self) -> bool:
        """Determine if call should be attempted based on state."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if time.time() - self._last_state_change >= self.config.timeout:
                self._transition_to(CircuitState.HALF_OPEN)
                return True
            return False
        
        if self.state == CircuitState.HALF_OPEN:
            return True
        
        return False
    
    def _transition_to(self, new_state: CircuitState):
        """Transition to new state with logging."""
        old_state = self.state
        self.state = new_state
        self._last_state_change = time.time()
        
        logger.info(
            f"Circuit breaker '{self.config.name}' transitioned: "
            f"{old_state.value} -> {new_state.value}"
        )
    
    def _record_success(self):
        """Record a successful call."""
        with self._lock:
            self.stats.success_count += 1
            self.stats.last_success_time = time.time()
            self.stats.total_successes += 1
            self.stats.total_calls += 1
            
            if self.state == CircuitState.HALF_OPEN:
                if self.stats.success_count >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
                    self.stats.success_count = 0
                    self.stats.failure_count = 0
    
    def _record_failure(self):
        """Record a failed call."""
        with self._lock:
            self.stats.failure_count += 1
            self.stats.last_failure_time = time.time()
            self.stats.total_failures += 1
            self.stats.total_calls += 1
            
            if self.state == CircuitState.CLOSED:
                if self.stats.failure_count >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
                    self.stats.failure_count = 0
                    self.stats.success_count = 0
            elif self.state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
                self.stats.failure_count = 0
                self.stats.success_count = 0
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function return value
            
        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: If function fails and exception is not expected
        """
        if not self._should_attempt_call():
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.config.name}' is OPEN. "
                f"Service appears to be unavailable."
            )
        
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except self.config.expected_exception as e:
            self._record_failure()
            raise
        except Exception as e:
            # Unexpected exceptions don't affect circuit state
            logger.warning(
                f"Unexpected exception in circuit breaker '{self.config.name}': {e}"
            )
            raise
    
    def get_state(self) -> CircuitState:
        """Get current circuit state."""
        return self.state
    
    def get_stats(self) -> CircuitBreakerStats:
        """Get circuit breaker statistics."""
        return self.stats
    
    def reset(self):
        """Reset circuit breaker to closed state."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.stats = CircuitBreakerStats()
            self._last_state_change = time.time()
            logger.info(f"Circuit breaker '{self.config.name}' reset to CLOSED state")


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open."""
    pass


# Circuit breaker registry for managing multiple breakers
class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers."""
    
    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = Lock()
    
    def register(self, name: str, config: CircuitBreakerConfig) -> CircuitBreaker:
        """Register a new circuit breaker."""
        config.name = name
        with self._lock:
            if name in self._breakers:
                logger.warning(f"Circuit breaker '{name}' already registered, replacing")
            breaker = CircuitBreaker(config)
            self._breakers[name] = breaker
            logger.info(f"Registered circuit breaker: {name}")
            return breaker
    
    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get a registered circuit breaker."""
        return self._breakers.get(name)
    
    def get_all_states(self) -> dict[str, str]:
        """Get states of all registered circuit breakers."""
        return {
            name: breaker.get_state().value 
            for name, breaker in self._breakers.items()
        }
    
    def reset_all(self):
        """Reset all circuit breakers."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()
        logger.info("All circuit breakers reset")


# Global registry instance
circuit_breaker_registry = CircuitBreakerRegistry()


# Decorator for circuit breaker protection
def with_circuit_breaker(breaker_name: str, config: Optional[CircuitBreakerConfig] = None):
    """
    Decorator to apply circuit breaker protection to a function.
    
    Args:
        breaker_name: Name of the circuit breaker
        config: Optional configuration (uses default if not provided)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            breaker = circuit_breaker_registry.get(breaker_name)
            
            if breaker is None:
                # Register with default config if not exists
                if config is None:
                    config = CircuitBreakerConfig(name=breaker_name)
                breaker = circuit_breaker_registry.register(breaker_name, config)
            
            return breaker.call(func, *args, **kwargs)
        
        return wrapper
    return decorator


# Pre-configured circuit breakers for common services
def setup_default_circuit_breakers():
    """Set up circuit breakers for common external services."""
    
    # Socrata API circuit breaker
    socrata_config = CircuitBreakerConfig(
        failure_threshold=3,
        success_threshold=2,
        timeout=30.0,
        expected_exception=Exception,  # Catch all for Socrata
        name="socrata_api"
    )
    circuit_breaker_registry.register("socrata_api", socrata_config)
    
    # LLM API circuit breaker
    llm_config = CircuitBreakerConfig(
        failure_threshold=5,
        success_threshold=3,
        timeout=60.0,
        expected_exception=Exception,
        name="llm_api"
    )
    circuit_breaker_registry.register("llm_api", llm_config)
    
    # ChromaDB circuit breaker
    chroma_config = CircuitBreakerConfig(
        failure_threshold=3,
        success_threshold=2,
        timeout=15.0,
        expected_exception=Exception,
        name="chroma_db"
    )
    circuit_breaker_registry.register("chroma_db", chroma_config)
    
    logger.info("Default circuit breakers configured")