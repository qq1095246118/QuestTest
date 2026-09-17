"""Lossless MySQL compressed transport, independent of business JSON assertions."""

import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from db.json_payload import decode_mysql_compressed_json
from db.factor4_lifecycle_repository import Factor4LifecycleRepository

pytestmark = pytest.mark.unit


def _compressed(raw: bytes) -> bytes:
    return len(raw).to_bytes(4, "little") + zlib.compress(raw)


@pytest.mark.parametrize("wrapper", [bytes, bytearray, memoryview])
def test_mysql_compressed_payload_preserves_exact_source_text(wrapper: Any) -> None:
    """Member order, absent/null keys, Unicode source and whitespace survive transport."""
    original = ' {"members":[{"factor_ref":"sub_factor:2","definition_source":"价格\\nreturn x"},{"factor_ref":"sub_factor:1","missing":null}],"factor_count":2} '
    assert decode_mysql_compressed_json(wrapper(_compressed(original.encode("utf-8")))) == original


@pytest.mark.parametrize("value", [None, {}, {"members": []}, '{"members": []}'])
def test_uncompressed_driver_and_repository_double_values_pass_through(value: Any) -> None:
    """Existing text and decoded JSON shapes retain their original representation."""
    assert decode_mysql_compressed_json(value) is value


def test_mysql_empty_source_and_optional_padding_are_preserved() -> None:
    """MySQL empty-string special case and optional terminal dot decode faithfully."""
    assert decode_mysql_compressed_json(b"") == ""
    assert decode_mysql_compressed_json(_compressed(b'{"x":1}') + b".") == '{"x":1}'


def test_lifecycle_snapshot_restores_full_text_before_business_consumers() -> None:
    """Whole-snapshot replay assertions still receive every original source byte."""
    original = ' {"members":[{"factor_ref":"sub_factor:1","definition_source":"return x\\n"}],"factor_count":1} '
    rows = iter([[{"id": 7, "factor_set_snapshot": _compressed(original.encode("utf-8"))}], [], []])
    tx = SimpleNamespace(
        execute=lambda *args: None,
        fetch_all=lambda *args: next(rows),
        fetch_one=lambda *args: {},
    )

    @contextmanager
    def transaction() -> Iterator[Any]:
        yield tx

    snapshot = Factor4LifecycleRepository(SimpleNamespace(transaction=transaction)).snapshot()
    assert snapshot.batches == ({"id": 7, "factor_set_snapshot": original},)


@pytest.mark.parametrize("damaged", [
    b"bad", b"\x08\x00\x00\x00broken",
    _compressed(b'{"secret":"must-not-appear"}')[:-2],
    (999).to_bytes(4, "little") + zlib.compress(b"{}"),
    (1).to_bytes(4, "little") + zlib.compress(b"{}"),
    _compressed(b"{}") + b"unexpected trailing text",
    _compressed(b"\xff"),
])
def test_malformed_compression_fails_without_echoing_payload(damaged: bytes) -> None:
    """Corruption, length mismatch, expansion beyond header and invalid UTF-8 fail closed."""
    with pytest.raises(ValueError) as captured:
        decode_mysql_compressed_json(damaged)
    assert str(captured.value) == "invalid compressed JSON transport"
