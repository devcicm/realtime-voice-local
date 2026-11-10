"""Tests for server/shared/result.py"""
import pytest
from server.shared.result import Result, of, of_async, Ok, Err


def test_ok_basic():
    r = of(lambda: 42)
    assert r.is_ok()
    assert not r.is_err()
    assert r.unwrap() == 42


def test_err_captures_exception():
    def boom():
        raise ValueError("kapow")
    r = of(boom)
    assert r.is_err()
    assert not r.is_ok()
    assert isinstance(r.error, ValueError)
    assert r.unwrap_or("default") == "default"


def test_map_preserves_ok():
    r = of(lambda: 5).map(lambda x: x * 2)
    assert r.unwrap() == 10


def test_map_swallows_exception_in_f():
    r = of(lambda: 5).map(lambda x: x / 0)
    assert r.is_err()


def test_map_skips_on_err():
    called = []
    r = of(lambda: (_ for _ in ()).throw(ValueError("e"))).map(lambda x: called.append(x))
    assert r.is_err()
    assert called == []


def test_flat_map():
    def chain(x):
        return of(lambda: x + 1)
    r = of(lambda: 1).flat_map(chain)
    assert r.unwrap() == 2


def test_unwrap_raises_on_err():
    r = Err("nope")
    with pytest.raises(ValueError, match="nope"):
        r.unwrap()


@pytest.mark.asyncio
async def test_of_async_ok():
    async def fetch():
        return "data"
    r = await of_async(fetch())
    assert r.is_ok()
    assert r.value == "data"


@pytest.mark.asyncio
async def test_of_async_err():
    async def boom():
        raise RuntimeError("net fail")
    r = await of_async(boom())
    assert r.is_err()
    assert isinstance(r.error, RuntimeError)


def test_chained_pipeline():
    """Realistic: parse -> validate -> transform."""
    def parse(s):
        return int(s + "0")  # bad input in middle
    def validate(n):
        if n < 0: raise ValueError("negative")
        return n
    def transform(n):
        return {"value": n}

    # Happy path
    r = of(lambda: parse("5")).map(validate).map(transform)
    assert r.is_ok()
    assert r.unwrap() == {"value": 50}

    # Negative path
    r = of(lambda: parse("-5")).map(validate).map(transform)
    assert r.is_err()

    # Parse failure (None + "0" -> TypeError)
    r = of(lambda: parse(None)).map(validate).map(transform)
    assert r.is_err()
