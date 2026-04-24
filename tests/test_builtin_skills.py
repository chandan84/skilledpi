"""Tests for built-in Pi agent skills using real filesystem operations."""

import os
import sys
import textwrap
from pathlib import Path

import pytest


def _load_skill(skill_path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("_skill", skill_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SKILLS_DIR = Path(__file__).parent.parent / "defaults" / "pi_skills"


@pytest.fixture(autouse=True)
def set_cwd(tmp_path, monkeypatch):
    """All skill calls run with tmp_path as CWD so path-safety checks work."""
    monkeypatch.chdir(tmp_path)


def test_read_file_returns_content(tmp_path):
    (tmp_path / "hello.txt").write_text("Hello Chibu!")
    mod = _load_skill(SKILLS_DIR / "read_file.py")
    result = mod.execute(path="hello.txt")
    assert result == "Hello Chibu!"


def test_read_file_rejects_path_traversal(tmp_path):
    mod = _load_skill(SKILLS_DIR / "read_file.py")
    result = mod.execute(path="../../../etc/passwd")
    assert result.startswith("Error:")


def test_write_file_creates_file(tmp_path):
    mod = _load_skill(SKILLS_DIR / "write_file.py")
    result = mod.execute(path="output/result.txt", content="42")
    assert (tmp_path / "output" / "result.txt").read_text() == "42"
    assert "42" in result or "Written" in result


def test_write_file_rejects_path_traversal(tmp_path):
    mod = _load_skill(SKILLS_DIR / "write_file.py")
    result = mod.execute(path="../../evil.txt", content="pwned")
    assert result.startswith("Error:")


def test_list_directory_returns_entries(tmp_path):
    (tmp_path / "a.py").touch()
    (tmp_path / "b.py").touch()
    (tmp_path / "subdir").mkdir()
    mod = _load_skill(SKILLS_DIR / "list_directory.py")
    entries = mod.execute(path=".")
    assert "a.py" in entries
    assert "b.py" in entries
    assert "subdir/" in entries


def test_list_directory_recursive(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("x")
    mod = _load_skill(SKILLS_DIR / "list_directory.py")
    entries = mod.execute(path=".", recursive=True)
    assert any("deep.txt" in e for e in entries)


def test_run_python_captures_output(tmp_path):
    mod = _load_skill(SKILLS_DIR / "run_python.py")
    result = mod.execute(code="print(2 + 2)")
    assert "4" in result


def test_run_python_blocks_os_import(tmp_path):
    mod = _load_skill(SKILLS_DIR / "run_python.py")
    result = mod.execute(code="import os; print(os.getcwd())")
    assert result.startswith("Error:")


def test_run_python_handles_runtime_error(tmp_path):
    mod = _load_skill(SKILLS_DIR / "run_python.py")
    result = mod.execute(code="raise ValueError('intentional')")
    assert "RuntimeError" in result or "intentional" in result
