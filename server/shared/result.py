"""Result<T, E> — functional result type for explicit error handling.

Mirrors ``client/src/result/result.js``. Use this in Python code paths so
errors are values, not exceptions.

Usage::

    from server.shared.result import Result, of, of_async

    # Synchronous
    res = of(lambda: int("abc"))
    assert res.is_err()
    assert res.error

    # Async
    res = await of_async(some_coro())
    if res.is_ok():
        do_thing(res.value)
    else:
        log.error(res.error)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar, Union, Awaitable

T = TypeVar("T")
E = TypeVar("E")


@dataclass
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool: return True
    def is_err(self) -> bool: return False
    def map(self, f: Callable[[T], Any]) -> "Result":
        try:
            return Ok(f(self.value))
        except Exception as exc:  # noqa: BLE001
            return Err(exc)
    def flat_map(self, f: Callable[[T], "Result"]) -> "Result":
        return f(self.value)
    def unwrap(self) -> T: return self.value
    def unwrap_or(self, default: T) -> T: return self.value
    def __repr__(self) -> str: return f"Ok({self.value!r})"


@dataclass
class Err(Generic[E]):
    error: E

    def is_ok(self) -> bool: return False
    def is_err(self) -> bool: return True
    def map(self, f: Callable[[Any], Any]) -> "Result": return self
    def flat_map(self, f: Callable[[Any], "Result"]) -> "Result": return self
    def unwrap(self) -> Any:
        raise ValueError(f"unwrap on Err: {self.error!r}")
    def unwrap_or(self, default: Any) -> Any: return default
    def __repr__(self) -> str: return f"Err({self.error!r})"


Result = Union[Ok[T], Err[E]]


def Ok_of(value: T) -> Ok[T]: return Ok(value)
def Err_of(error: E) -> Err[E]: return Err(error)


def of(fn: Callable[[], T]) -> Result[T, Exception]:
    """Wrap a sync call so its exception becomes an Err."""
    try:
        return Ok(fn())
    except Exception as exc:  # noqa: BLE001
        return Err(exc)


async def of_async(coro: Awaitable[T]) -> Result[T, Exception]:
    """Wrap an async call so its exception becomes an Err."""
    try:
        return Ok(await coro)
    except Exception as exc:  # noqa: BLE001
        return Err(exc)


def from_optional(value, error: E = "value is None") -> Result:
    return Ok(value) if value is not None else Err(error)
