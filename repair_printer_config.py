#!/usr/bin/env python3
"""Keep filament-sync configuration above Klipper's SAVE_CONFIG boundary."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


SAVE_CONFIG_MARKER = re.compile(r"^\s*#\*#\s*<[- ]*SAVE_CONFIG[- ]*>\s*$", re.IGNORECASE)
SAVE_VARIABLES_HEADER = re.compile(r"^\s*\[save_variables\]\s*(?:#.*)?$", re.IGNORECASE)
SECTION_HEADER = re.compile(r"^\s*\[[^]]+\]")


def marker_index(lines: list[str]) -> int:
    return next((index for index, line in enumerate(lines) if SAVE_CONFIG_MARKER.match(line.rstrip("\n"))), len(lines))


def find_save_variables(lines: list[str]) -> int | None:
    return next(
        (index for index, line in enumerate(lines) if SAVE_VARIABLES_HEADER.match(line.rstrip("\n"))),
        None,
    )


def extract_section(lines: list[str], start: int) -> tuple[list[str], list[str]]:
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if SECTION_HEADER.match(line) or SAVE_CONFIG_MARKER.match(line.rstrip("\n")):
            break
        if line.strip() == "":
            end += 1
            break
        end += 1
    return lines[start:end], lines[:start] + lines[end:]


def normalize_spacing(block: list[str]) -> list[str]:
    normalized = [line if line.endswith("\n") else line + "\n" for line in block]
    while normalized and normalized[-1].strip() == "":
        normalized.pop()
    return normalized


def repaired_text(source: str, include: str, variables_file: Path, external_save_variables: bool = False) -> str:
    lines = source.splitlines(keepends=True)
    existing_save_index = find_save_variables(lines)
    save_block: list[str] | None = None
    if existing_save_index is not None and existing_save_index >= marker_index(lines):
        save_block, lines = extract_section(lines, existing_save_index)

    lines = [line for line in lines if line.strip() != include]
    insertion = marker_index(lines)
    blocks: list[list[str]] = []

    if find_save_variables(lines) is None and (save_block is not None or not external_save_variables):
        if save_block is None:
            save_block = ["[save_variables]\n", f"filename: {variables_file}\n"]
        blocks.append(normalize_spacing(save_block))
    blocks.append([include + "\n"])

    prefix = lines[:insertion]
    suffix = lines[insertion:]
    while prefix and prefix[-1].strip() == "":
        prefix.pop()
    inserted: list[str] = []
    for block in blocks:
        if inserted:
            inserted.append("\n")
        inserted.extend(block)
    return "".join(prefix + ["\n"] + inserted + ["\n"] + suffix)


def atomic_write(path: Path, value: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--include", required=True)
    parser.add_argument("--variables-file", required=True, type=Path)
    parser.add_argument("--external-save-variables", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    repaired = repaired_text(source, args.include, args.variables_file, args.external_save_variables)
    if source == repaired:
        return 0
    if args.check:
        return 1
    atomic_write(args.path, repaired)
    print(f"Placed filament-sync configuration above the SAVE_CONFIG boundary in {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
