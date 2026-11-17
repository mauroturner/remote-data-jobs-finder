"""Convert the resume placed in your-resume/ to Markdown with Microsoft MarkItDown.

Usage: python -m jobfinder.resume [--dir your-resume] [--out your-resume/resume.md]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

SUPPORTED = {".pdf", ".docx", ".doc", ".pptx", ".html", ".htm", ".txt", ".rtf", ".odt"}
OUTPUT_NAME = "resume.md"
IGNORED = {"README.md", OUTPUT_NAME, ".gitkeep"}


def find_resume(folder: Path) -> Path:
    """Return the most recently modified supported file in the folder."""
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    candidates = [
        p for p in folder.iterdir()
        if p.is_file() and p.name not in IGNORED and p.suffix.lower() in SUPPORTED
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No resume found in {folder}. Add one file ({', '.join(sorted(SUPPORTED))})."
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _markitdown(path: Path) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError:
        sys.exit("markitdown is not installed. Run: pip install -r requirements.txt")
    # Plugins disabled: only the built-in converters run on the file.
    return MarkItDown(enable_plugins=False).convert(str(path)).markdown


def convert(folder: Path, out: Path, converter: Callable[[Path], str] = _markitdown) -> Path:
    source = find_resume(folder)
    markdown = converter(source).strip()
    if not markdown:
        raise ValueError(f"{source.name} produced no text (scanned image PDF?). Export it as a text PDF or DOCX.")
    out.write_text(f"<!-- Generated from {source.name} by markitdown. Do not edit; re-run to refresh. -->\n\n"
                   f"{markdown}\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert your resume to Markdown with MarkItDown.")
    parser.add_argument("--dir", type=Path, default=Path("your-resume"))
    parser.add_argument("--out", type=Path, default=None, help="default: <dir>/resume.md")
    args = parser.parse_args(argv)
    try:
        out = convert(args.dir, args.out or args.dir / OUTPUT_NAME)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Resume converted: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
