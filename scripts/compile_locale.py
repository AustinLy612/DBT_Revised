"""Compile the checked-in English PO catalog without a system gettext dependency."""

from __future__ import annotations

import ast
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "locale" / "en" / "LC_MESSAGES" / "django.po"
OUTPUT = SOURCE.with_suffix(".mo")


def read_catalog(path: Path) -> dict[str, str]:
    catalog: dict[str, str] = {}
    key = value = ""
    field = None

    def finish() -> None:
        if value:
            if key in catalog:
                raise ValueError(f"Duplicate translation: {key}")
            catalog[key] = value

    for line in [*path.read_text(encoding="utf-8").splitlines(), ""]:
        if not line:
            finish()
            key = value = ""
            field = None
        elif line.startswith("msgid "):
            key = ast.literal_eval(line[6:])
            field = "id"
        elif line.startswith("msgstr "):
            value = ast.literal_eval(line[7:])
            field = "str"
        elif line.startswith('"'):
            if field == "id":
                key += ast.literal_eval(line)
            elif field == "str":
                value += ast.literal_eval(line)
        elif not line.startswith("#"):
            raise ValueError(f"Unsupported PO entry: {line}")
    return catalog


def compile_catalog(catalog: dict[str, str]) -> bytes:
    items = sorted((key.encode("utf-8"), value.encode("utf-8")) for key, value in catalog.items())
    count = len(items)
    id_table = 28
    str_table = id_table + 8 * count
    offset = str_table + 8 * count
    id_records = []
    str_records = []
    payload = bytearray()
    for key, _ in items:
        id_records.append((len(key), offset + len(payload)))
        payload.extend(key + b"\0")
    for _, value in items:
        str_records.append((len(value), offset + len(payload)))
        payload.extend(value + b"\0")
    header = struct.pack("<7I", 0x950412DE, 0, count, id_table, str_table, 0, 0)
    tables = b"".join(struct.pack("<2I", *record) for record in (*id_records, *str_records))
    return header + tables + payload


if __name__ == "__main__":
    entries = read_catalog(SOURCE)
    OUTPUT.write_bytes(compile_catalog(entries))
    print(f"Compiled {len(entries)} English translations to {OUTPUT}")
