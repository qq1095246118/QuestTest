"""Lossless decoding of JSON text sent by MySQL COMPRESS expressions."""

from typing import Any
import zlib


def decode_mysql_compressed_json(value: Any) -> Any:
    """Restore exact JSON text from a MySQL COMPRESS result.

    Binary inputs contain a four-byte little-endian uncompressed byte length,
    followed by a zlib stream and MySQL's optional trailing space-protection
    dot. Empty binary input represents an empty source string. Existing text,
    decoded objects and SQL NULL pass through unchanged for compatible drivers
    and repository doubles. Returns the original UTF-8 text for binary inputs;
    malformed framing, length, compression or UTF-8 raises a content-free
    ValueError. JSON parsing and object-shape validation remain with consumers.
    """
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return value
    raw = bytes(value)
    if not raw:
        return ""
    if len(raw) < 5:
        raise ValueError("invalid compressed JSON transport")
    expected = int.from_bytes(raw[:4], "little")
    try:
        stream = zlib.decompressobj()
        restored = stream.decompress(raw[4:], expected + 1)
        if (not stream.eof or stream.unconsumed_tail
                or stream.unused_data not in (b"", b".") or len(restored) != expected):
            raise ValueError("invalid compressed JSON transport")
        return restored.decode("utf-8", "strict")
    except (zlib.error, UnicodeDecodeError):
        raise ValueError("invalid compressed JSON transport") from None
