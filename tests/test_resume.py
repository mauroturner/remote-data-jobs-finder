import os
from pathlib import Path

import pytest

from jobfinder.resume import convert, find_resume, main


def touch(folder: Path, name: str, mtime: int) -> Path:
    path = folder / name
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_find_resume_picks_newest_supported_file_and_skips_generated(tmp_path):
    touch(tmp_path, "old.pdf", 1_000)
    newest = touch(tmp_path, "cv.docx", 2_000)
    touch(tmp_path, "resume.md", 3_000)   # generated output
    touch(tmp_path, "README.md", 3_000)   # folder docs
    touch(tmp_path, "photo.png", 4_000)   # unsupported
    assert find_resume(tmp_path) == newest


def test_find_resume_errors_when_empty(tmp_path):
    with pytest.raises(FileNotFoundError, match="No resume found"):
        find_resume(tmp_path)


def test_convert_writes_markdown_with_header(tmp_path):
    touch(tmp_path, "cv.pdf", 1_000)
    out = convert(tmp_path, tmp_path / "resume.md", converter=lambda p: "# Jane Doe\nPython, SQL")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!-- Generated from cv.pdf")
    assert "# Jane Doe\nPython, SQL" in text


def test_convert_rejects_empty_text(tmp_path):
    touch(tmp_path, "scan.pdf", 1_000)
    with pytest.raises(ValueError, match="produced no text"):
        convert(tmp_path, tmp_path / "resume.md", converter=lambda p: "   ")


def test_main_converts_real_html_with_markitdown(tmp_path, capsys):
    pytest.importorskip("markitdown")
    (tmp_path / "cv.html").write_text("<h1>Jane Doe</h1><p>Data Analyst. Python, SQL.</p>", encoding="utf-8")
    assert main(["--dir", str(tmp_path)]) == 0
    assert "Jane Doe" in (tmp_path / "resume.md").read_text(encoding="utf-8")
    assert "Resume converted" in capsys.readouterr().out


def test_main_returns_error_code_without_resume(tmp_path, capsys):
    assert main(["--dir", str(tmp_path)]) == 1
    assert "No resume found" in capsys.readouterr().err
