"""Unit tests for the filesystem connector (path jail + sensitive file guards)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from uuid import uuid4

import pytest
import regex as safe_regex

import connectors.filesystem.connector as filesystem_module
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


@pytest.mark.asyncio
async def test_write_schema_uses_instance_max_file_size(tmp_path):
    connector = FilesystemConnector(allowed_root=str(tmp_path), max_file_size=123)
    tools = await connector.list_tools()
    write = next(tool for tool in tools if tool.name == "filesystem.write")

    assert write.input_schema["properties"]["content"]["maxLength"] == 123


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

    @pytest.mark.asyncio
    async def test_list_stops_at_configured_max_entries(self, tmp_path, monkeypatch):
        for index in range(5):
            (tmp_path / f"{index}.txt").write_text("x")

        real_scandir = os.scandir
        yielded = 0
        resolved = 0

        class CountingScandir:
            def __init__(self, path):
                self._iterator = real_scandir(path)

            def __enter__(self):
                self._iterator.__enter__()
                return self

            def __exit__(self, *args):
                return self._iterator.__exit__(*args)

            def __iter__(self):
                return self

            def __next__(self):
                nonlocal yielded
                entry = next(self._iterator)
                yielded += 1
                return entry

        monkeypatch.setattr(filesystem_module.os, "scandir", CountingScandir)
        real_realpath = os.path.realpath

        def counting_realpath(path):
            nonlocal resolved
            if os.path.basename(os.fspath(path)).endswith(".txt"):
                resolved += 1
            return real_realpath(path)

        limited = FilesystemConnector(allowed_root=str(tmp_path), max_entries=2)
        resolved = 0
        monkeypatch.setattr(filesystem_module.os.path, "realpath", counting_realpath)
        result = await limited.execute("filesystem.list", {"path": "."}, ctx())

        assert result.success
        assert len(result.data["entries"]) == 2
        assert result.data["truncated"] is True
        assert yielded == 3
        assert resolved == 2

    @pytest.mark.asyncio
    async def test_list_exact_budget_without_more_is_not_truncated(self, tmp_path):
        for index in range(2):
            (tmp_path / f"{index}.txt").write_text("x")

        limited = FilesystemConnector(allowed_root=str(tmp_path), max_entries=2)
        result = await limited.execute("filesystem.list", {"path": "."}, ctx())

        assert result.success
        assert len(result.data["entries"]) == 2
        assert result.data["truncated"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("blocked_name", [".env", "outside-link"])
    async def test_list_blocked_entries_consume_scan_budget(
        self, tmp_path, blocked_name, monkeypatch
    ):
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        if blocked_name == ".env":
            (tmp_path / blocked_name).write_text("SECRET=1")
        else:
            try:
                (tmp_path / blocked_name).symlink_to(outside, target_is_directory=True)
            except OSError:
                pytest.skip("symlinks are unavailable on this host")
        (tmp_path / "visible.txt").write_text("x")

        real_scandir = os.scandir

        class SortedScandir:
            def __init__(self, path):
                with real_scandir(path) as iterator:
                    self._entries = iter(sorted(iterator, key=lambda entry: entry.name))

            def __enter__(self):
                return self._entries

            def __exit__(self, *_args):
                return None

        monkeypatch.setattr(filesystem_module.os, "scandir", SortedScandir)
        limited = FilesystemConnector(allowed_root=str(tmp_path), max_entries=1)
        result = await limited.execute("filesystem.list", {"path": "."}, ctx())

        assert result.success
        assert result.data["entries"] == []
        assert result.data["truncated"] is True

    def test_list_clamps_configured_max_entries_to_hard_cap(self, tmp_path):
        limited = FilesystemConnector(allowed_root=str(tmp_path), max_entries=10**9)

        assert limited.max_entries == filesystem_module.HARD_MAX_ENTRIES

    @pytest.mark.asyncio
    async def test_list_does_not_block_event_loop(self, connector, monkeypatch):
        original_list = connector._list

        def slow_list(arguments):
            time.sleep(0.05)
            return original_list(arguments)

        monkeypatch.setattr(connector, "_list", slow_list)
        list_task = asyncio.create_task(
            connector.execute("filesystem.list", {"path": "."}, ctx())
        )
        await asyncio.sleep(0.01)

        assert list_task.done() is False
        assert (await list_task).success


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
    @pytest.mark.parametrize("target_name", [".env", "secret.txt"])
    async def test_search_skips_plain_named_symlink_to_sensitive_file(
        self, connector, root, target_name
    ):
        target = root / target_name
        target.write_text("SYMLINK_SECRET\n", encoding="utf-8")
        try:
            (root / "safe.txt").symlink_to(target)
        except OSError:
            pytest.skip("symlinks are unavailable on this host")

        result = await connector.execute(
            "filesystem.search", {"path": ".", "pattern": "SYMLINK_SECRET"}, ctx()
        )

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

    @pytest.mark.asyncio
    async def test_write_limit_uses_encoded_bytes(self, tmp_path):
        limited = FilesystemConnector(allowed_root=str(tmp_path), max_file_size=3)
        result = await limited.execute(
            "filesystem.write",
            {"path": "out.txt", "content": "éé", "encoding": "utf-8"},
            ctx(),
        )

        assert result.success is False
        assert result.error_code == "FILE_TOO_LARGE"
        assert not (tmp_path / "out.txt").exists()

    @pytest.mark.asyncio
    async def test_write_uses_exact_encoded_bytes_without_newline_translation(
        self, tmp_path, monkeypatch
    ):
        real_fdopen = os.fdopen

        def binary_fdopen(descriptor, mode, **kwargs):
            assert mode == "wb"
            assert "encoding" not in kwargs
            return real_fdopen(descriptor, mode, **kwargs)

        monkeypatch.setattr(filesystem_module.os, "fdopen", binary_fdopen)
        content = "first\nsecond\n"
        encoded = content.encode("utf-8")
        limited = FilesystemConnector(
            allowed_root=str(tmp_path), max_file_size=len(encoded)
        )
        result = await limited.execute(
            "filesystem.write",
            {"path": "out.txt", "content": content, "encoding": "utf-8"},
            ctx(),
        )

        assert result.success
        assert (tmp_path / "out.txt").read_bytes() == encoded
        assert result.data["size_bytes"] == len(encoded)

    @pytest.mark.asyncio
    async def test_darwin_fd_path_failure_does_not_truncate_or_write(
        self, tmp_path, monkeypatch
    ):
        fcntl = pytest.importorskip("fcntl")
        target = tmp_path / "out.txt"
        target.write_text("original", encoding="utf-8")
        connector = FilesystemConnector(allowed_root=str(tmp_path))

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(
            fcntl,
            "fcntl",
            lambda *_args: (_ for _ in ()).throw(OSError("F_GETPATH failed")),
        )
        result = await connector.execute(
            "filesystem.write",
            {"path": "out.txt", "content": "replacement", "overwrite": True},
            ctx(),
        )

        assert result.success is False
        assert target.read_text(encoding="utf-8") == "original"


def test_darwin_opened_path_uses_f_getpath(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    target = tmp_path / "out.txt"
    target.write_text("data", encoding="utf-8")
    connector = FilesystemConnector(allowed_root=str(tmp_path))

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        fcntl,
        "fcntl",
        lambda *_args: os.fsencode(target) + b"\0ignored",
    )
    with target.open() as handle:
        assert connector._opened_path(handle) == os.path.realpath(target)


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
