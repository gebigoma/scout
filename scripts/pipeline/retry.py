import random
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def with_backoff(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> T:
    """Call fn(), retrying on exception with exponential backoff + jitter.
    Raises the last exception if all attempts are exhausted."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if on_retry:
                on_retry(attempt, e)
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc
