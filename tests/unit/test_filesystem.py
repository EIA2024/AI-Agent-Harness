"""Unit tests for the filesystem connector (path jail + sensitive file guards)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import regex as safe_regex

from connectors.filesystem.connector import FilesystemConnector
from personal_ai_os.common.models import ToolExecutionContext


def ctx() -> ToolExecutionContext:
    return ToolExecutionContext(run_id=uuid4(), owner_id=uuid4())


@pytest.fixture
def root(tmp_path):
    (tmp_path / "notes.txt").write_text("hello world\nsecond line\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "id_rsa.pem").write_text("PRIVATE\n", encoding="utf-8")
    (tmp_path / "credentials.txt").write_text("user:pass\n", encoding="utf-8")
    (tmp_path / "key.pem").write_text("KEY\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("needle in haystack\nplain\n", encoding="utf-8")
    (sub / "b.txt").write_text("nothing here\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def connector(root) -> FilesystemConnector:
    return FilesystemConnector(allowed_root=str(root))


@pytest.mark.asyncio
async def test_list_tools(connector):
    tools = await connector.list_tools()
    names = {t.name for t in tools}
    assert names == {"filesystem.read", "filesystem.list", "filesystem.search", "filesystem.write"}
    by_name = {t.name: t for t in tools}
    assert by_name["filesystem.read"].risk_level == 1
    assert by_name["filesystem.write"].risk_level == 2
    assert by_name["filesystem.write"].side_effect is True


class TestRead:
    @pytest.mark.asyncio
    async def test_read_file(self, connector):
        result = await connector.execute("filesystem.read", {"path": "notes.txt"}, ctx())
        assert result.success
        assert result.text == "hello world\nsecond line\n"
        assert result.data["line_count"] == 2

    @pytest.mark.asyncio
    async def test_read_missing_file(self, connector):
        result = await connector.execute("filesystem.read", {"path": "nope.txt"}, ctx())
        assert result.success is False
        assert result.error_code == "FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_read_max_lines_truncates(self, connector):
        result = await connector.execute(
            "filesystem.read", {"path": "notes.txt", "max_lines": 1}, ctx()
        )
        assert result.success
        assert result.text == "hello world\n"
        assert result.data["truncated"] is True

    @pytest.mark.asyncio
    async def test_read_absolute_path_inside_root(self, connector, root):
        result = await connector.execute("filesystem.read", {"path": str(root / "notes.txt")}, ctx())
        assert result.success

    @pytest.mark.asyncio
    async def test_read_sensitive_files_rejected(self, connector):
        for sensitive in [".env", "id_rsa.pem", "credentials.txt", "key.pem"]:
            result = await connector.execute("filesystem.read", {"path": sensitive}, ctx())
            assert result.success is False, f"should reject {sensitive}"
            assert result.error_code == "SENSITIVE_FILE"

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, connector):
        for evil in ["../outside.txt", "../../etc/passwd", "..", os.path.join("sub", "..", "..", "x.txt")]:
            result = await connector.execute("filesystem.read", {"path": evil}, ctx())
            assert result.success is False
            assert "escapes the allowed root" in result.error

    @pytest.mark.asyncio
    async def test_symlink_is_rejected_even_when_target_is_inside_root(self, connector, root):
        link = root / "notes-link.txt"
        try:
            link.symlink_to(root / "notes.txt")
        except OSError:
            pytest.skip("symlinks are unavailable on this host")
        result = await connector.execute(
            "filesystem.read", {"path": "notes-link.txt"}, ctx()
        )
        assert result.success is False
        assert "symbolic link" in result.error


class TestList:
    @pytest.mark.asyncio
    async def test_list_directory(self, connector):
        result = await connector.execute("filesystem.list", {"path": "."}, ctx())
        assert result.success
        names = {e["name"] for e in result.data["entries"]}
        assert "notes.txt" in names and "sub" in names

    @pytest.mark.asyncio
    async def test_list_recursive(self, connector):
        result = await connector.execute(
            "filesystem.list", {"path": ".", "recursive": True, "max_depth": 3}, ctx()
        )
        assert result.success
        names = {e["name"] for e in result.data["entries"]}
        assert {"a.txt", "b.txt"} <= names

    @pytest.mark.asyncio
    async def test_list_missing_directory(self, connector):
        result = await connector.execute("filesystem.list", {"path": "missing"}, ctx())
        assert result.success is False
        assert result.error_code == "DIRECTORY_NOT_FOUND"


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_finds_match(self, connector):
        result = await connector.execute("filesystem.search", {"path": ".", "pattern": "needle"}, ctx())
        assert result.success
        matches = result.data["matches"]
        assert len(matches) == 1
        assert matches[0]["file"].endswith(os.path.join("sub", "a.txt"))
        assert matches[0]["line"] == 1

    @pytest.mark.asyncio
    async def test_search_no_match(self, connector):
        result = await connector.execute("filesystem.search", {"path": ".", "pattern": "zzz-nothing"}, ctx())
        assert result.success
        assert result.data["matches"] == []

    @pytest.mark.asyncio
    async def test_search_invalid_regex_mode_fails(self, connector):
        result = await connector.execute(
            "filesystem.search", {"path": ".", "pattern": "(", "mode": "regex"}, ctx()
        )
        assert result.success is False
        assert "invalid regex" in result.error

    async def test_search_literal_parenthesis_is_valid(self, connector):
        # P1-003: literal is the default — "(" is a fine substring, not a regex
        result = await connector.execute("filesystem.search", {"path": ".", "pattern": "("}, ctx())
        assert result.success is True

    @pytest.mark.asyncio
    async def test_search_skips_sensitive_files(self, connector):
        # "SECRET" appears only in .env which must be skipped
        result = await connector.execute("filesystem.search", {"path": ".", "pattern": "SECRET"}, ctx())
        assert result.success
        assert result.data["matches"] == []

    @pytest.mark.asyncio
    async def test_regex_timeout_fails_closed(self, connector, monkeypatch):
        class SlowRegex:
            def search(self, line, *, timeout):
                raise TimeoutError

        monkeypatch.setattr(safe_regex, "compile", lambda pattern: SlowRegex())
        result = await connector.execute(
            "filesystem.search",
            {"path": ".", "pattern": "(a+)+$", "mode": "regex"},
            ctx(),
        )
        assert result.success is False
        assert result.error_code == "REGEX_TIMEOUT"


class TestWrite:
    @pytest.mark.asyncio
    async def test_write_new_file(self, connector, root):
        result = await connector.execute("filesystem.write", {"path": "out.txt", "content": "data"}, ctx())
        assert result.success
        assert (root / "out.txt").read_text(encoding="utf-8") == "data"

    @pytest.mark.asyncio
    async def test_write_refuses_overwrite(self, connector):
        result = await connector.execute("filesystem.write", {"path": "notes.txt", "content": "clobber"}, ctx())
        assert result.success is False
        assert result.error_code == "FILE_EXISTS"

    @pytest.mark.asyncio
    async def test_write_overwrite_allowed(self, connector, root):
        result = await connector.execute(
            "filesystem.write", {"path": "notes.txt", "content": "new", "overwrite": True}, ctx()
        )
        assert result.success
        assert (root / "notes.txt").read_text(encoding="utf-8") == "new"

    @pytest.mark.asyncio
    async def test_write_nested_dir_created(self, connector, root):
        result = await connector.execute("filesystem.write", {"path": "deep/nested/out.txt", "content": "x"}, ctx())
        assert result.success
        assert (root / "deep" / "nested" / "out.txt").read_text(encoding="utf-8") == "x"

    @pytest.mark.asyncio
    async def test_write_path_traversal_rejected(self, connector):
        result = await connector.execute(
            "filesystem.write", {"path": "../evil.txt", "content": "x"}, ctx()
        )
        assert result.success is False
        assert "escapes the allowed root" in result.error

    @pytest.mark.asyncio
    async def test_write_sensitive_rejected(self, connector):
        result = await connector.execute("filesystem.write", {"path": ".env", "content": "x"}, ctx())
        assert result.success is False
        assert result.error_code == "SENSITIVE_FILE"


class TestSizeLimit:
    @pytest.mark.asyncio
    async def test_read_refuses_oversized_file(self, tmp_path):
        big = tmp_path / "big.txt"
        big.write_text("x" * 5000, encoding="utf-8")
        connector = FilesystemConnector(allowed_root=str(tmp_path), max_file_size=1000)
        result = await connector.execute("filesystem.read", {"path": "big.txt"}, ctx())
        assert result.success is False
        assert "file too large" in result.error


async def test_list_summary_includes_example_names(tmp_path):
    (tmp_path / "alpha.txt").write_text("x")
    (tmp_path / "beta.txt").write_text("y")
    (tmp_path / "gamma.txt").write_text("z")
    c = FilesystemConnector(allowed_root=str(tmp_path))
    result = await c.execute("filesystem.list", {"path": "."}, ctx())
    assert result.text == "3 entries (e.g. alpha.txt, beta.txt, gamma.txt)"


async def test_search_summary_includes_example_files(tmp_path):
    (tmp_path / "one.py").write_text("needle here\n")
    (tmp_path / "two.py").write_text("nope\n")
    c = FilesystemConnector(allowed_root=str(tmp_path))
    result = await c.execute("filesystem.search", {"path": ".", "pattern": "needle"}, ctx())
    assert "1 match(es)" in result.text
    assert "one.py" in result.text


def test_connector_requires_explicit_root(monkeypatch):
    """P1-004 — no silent cwd root."""
    monkeypatch.delenv("PERSONAL_AI_WORKSPACE_ROOT", raising=False)
    with pytest.raises(ValueError, match="WORKSPACE_ROOT"):
        FilesystemConnector()


def test_connector_uses_workspace_root_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONAL_AI_WORKSPACE_ROOT", str(tmp_path))
    c = FilesystemConnector()
    assert c.allowed_root == os.path.realpath(str(tmp_path))


async def test_list_clamps_requested_depth(tmp_path):
    """P1-002 — a model cannot request an unbounded recursive walk."""

    from connectors.filesystem.connector import HARD_MAX_DEPTH

    # build a tree deeper than the hard ceiling
    node = tmp_path
    for i in range(HARD_MAX_DEPTH + 2):
        node = node / f"d{i}"
        node.mkdir()
    (node / "leaf.txt").write_text("x")

    c = FilesystemConnector(allowed_root=str(tmp_path))
    result = await c.execute(
        "filesystem.list", {"path": ".", "recursive": True, "max_depth": 999}, ctx()
    )
    assert result.success
    # walked at most HARD_MAX_DEPTH levels (no leaf.txt, no crash)
    assert "leaf.txt" not in str(result.data)


async def test_search_defaults_to_literal_and_supports_regex(tmp_path):
    (tmp_path / "a.py").write_text("import os\n")
    (tmp_path / "b.py").write_text("needle here\n")
    c = FilesystemConnector(allowed_root=str(tmp_path))
    # literal default
    res = await c.execute("filesystem.search", {"path": ".", "pattern": "needle"}, ctx())
    assert res.success and len(res.data["matches"]) == 1
    assert res.data["matches"][0]["file"] == "b.py"
    # explicit regex mode
    res2 = await c.execute("filesystem.search", {"path": ".", "pattern": "^import", "mode": "regex"}, ctx())
    assert res2.success and len(res2.data["matches"]) == 1
    assert res2.data["matches"][0]["file"] == "a.py"
    # invalid mode fails, not executes
    res3 = await c.execute("filesystem.search", {"path": ".", "pattern": "x", "mode": "bogus"}, ctx())
    assert res3.success is False


async def test_sensitive_files_never_searched(tmp_path):
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / "secret_token.txt").write_text("tok\n")
    (tmp_path / "ok.txt").write_text("SECRET is fine\n")
    c = FilesystemConnector(allowed_root=str(tmp_path))
    res = await c.execute("filesystem.search", {"path": ".", "pattern": "SECRET"}, ctx())
    assert res.success
    files = [m["file"] for m in res.data["matches"]]
    assert "ok.txt" in files
    assert ".env" not in files
    assert "secret_token.txt" not in files
