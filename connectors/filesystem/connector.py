"""Filesystem connector: read / list / search / write within a jailed root.

Every path is resolved with ``os.path.realpath`` and must stay inside the
``allowed_root`` configured at construction (blocks ``../`` escape). Sensitive
files (``.env``, ``*.pem``, ``*id_rsa*``, ``*credentials*``, ``*.key``) are
never read or searched. Reads refuse files larger than ``max_file_size``.
"""

from __future__ import annotations

import os
import re

from personal_ai_os.common.models import ToolDescriptor, ToolExecutionContext, ToolResult

DEFAULT_MAX_FILE_SIZE = 1024 * 1024  # 1 MiB
DEFAULT_MAX_LINES = 1000
DEFAULT_MAX_RESULTS = 200
DEFAULT_MAX_DEPTH = 8

_SENSITIVE_PATTERNS = [
    re.compile(r"^\.env(\.|$)", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"id_rsa", re.IGNORECASE),
    re.compile(r"credentials", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
]


def _examples_summary(items: list[str], *, limit: int = 8) -> str:
    """Human preview of the first few names, e.g. ``(e.g. src, docs, tests…)``."""
    shown = items[:limit]
    if not shown:
        return ""
    suffix = "…" if len(items) > limit else ""
    return f" (e.g. {', '.join(shown)}{suffix})"


class FilesystemConnector:
    """Exposes filesystem.read/list/search/write tools rooted at ``allowed_root``."""

    connector_name = "filesystem"

    def __init__(
        self,
        *,
        allowed_root: str | None = None,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self.allowed_root = os.path.realpath(allowed_root or os.getcwd())
        self.max_file_size = max_file_size
        self.max_depth = max_depth

    # ------------------------------------------------------------------
    # Tool descriptors
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="filesystem.read",
                namespace="filesystem",
                description="Read a text file (safely, within the allowed root) and return its contents.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to allowed root"},
                        "encoding": {"type": "string", "default": "utf-8"},
                        "max_lines": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_LINES},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                risk_level=1,
                side_effect=False,
                destructive=False,
                external_write=False,
                idempotent=True,
                timeout_seconds=10,
                retry_policy="none",
                result_trust="trusted_tool",
                tags=["filesystem", "read", "file"],
            ),
            ToolDescriptor(
                name="filesystem.list",
                namespace="filesystem",
                description="List files and directories under a path.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path relative to allowed root"},
                        "recursive": {"type": "boolean", "default": False},
                        "max_depth": {"type": "integer", "minimum": 1, "default": 3},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                risk_level=1,
                side_effect=False,
                destructive=False,
                external_write=False,
                idempotent=True,
                timeout_seconds=10,
                retry_policy="none",
                result_trust="trusted_tool",
                tags=["filesystem", "list", "directory"],
            ),
            ToolDescriptor(
                name="filesystem.search",
                namespace="filesystem",
                description="Grep-style content search over files within a directory.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory to search, relative to allowed root"},
                        "pattern": {"type": "string", "description": "Regular expression to match against file lines"},
                        "max_results": {"type": "integer", "minimum": 1, "default": 50},
                    },
                    "required": ["path", "pattern"],
                    "additionalProperties": False,
                },
                risk_level=1,
                side_effect=False,
                destructive=False,
                external_write=False,
                idempotent=True,
                timeout_seconds=30,
                retry_policy="none",
                result_trust="trusted_tool",
                tags=["filesystem", "search", "grep"],
            ),
            ToolDescriptor(
                name="filesystem.write",
                namespace="filesystem",
                description="Write content to a file. Refuses to overwrite an existing file unless overwrite=true.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Destination path relative to allowed root"},
                        "content": {"type": "string"},
                        "encoding": {"type": "string", "default": "utf-8"},
                        "overwrite": {"type": "boolean", "default": False},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                risk_level=2,
                side_effect=True,
                destructive=False,
                external_write=True,
                idempotent=False,
                timeout_seconds=10,
                retry_policy="none",
                result_trust="trusted_tool",
                tags=["filesystem", "write", "file"],
            ),
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        short = tool.split(".")[-1] if "." in tool else tool
        try:
            if short == "read":
                return self._read(arguments)
            if short == "list":
                return self._list(arguments)
            if short == "search":
                return self._search(arguments)
            if short == "write":
                return self._write(arguments)
            return ToolResult.fail(error=f"Unknown filesystem tool: {tool}", error_code="UNKNOWN_TOOL")
        except ValueError as exc:
            return ToolResult.fail(error=str(exc), error_code="FILESYSTEM_ERROR")
        except OSError as exc:
            return ToolResult.fail(error=f"Filesystem error: {exc}", error_code="FILESYSTEM_ERROR")

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------

    def _resolve_path(self, path: str) -> str:
        """Return the realpath of ``path`` (absolute or relative to allowed_root),
        enforcing containment inside ``allowed_root``."""
        candidate = path if os.path.isabs(path) else os.path.join(self.allowed_root, path)
        resolved = os.path.realpath(candidate)
        root = os.path.normcase(os.path.realpath(self.allowed_root))
        if os.path.commonpath([root, os.path.normcase(resolved)]) != root:
            raise ValueError(f"path {path!r} escapes the allowed root")
        return resolved

    @staticmethod
    def _is_sensitive(basename: str) -> bool:
        return any(p.search(basename) for p in _SENSITIVE_PATTERNS)

    def _read(self, arguments: dict) -> ToolResult:
        path = arguments.get("path")
        encoding = arguments.get("encoding", "utf-8")
        max_lines = arguments.get("max_lines", DEFAULT_MAX_LINES)
        resolved = self._resolve_path(path)
        if not os.path.isfile(resolved):
            return ToolResult.fail(error=f"not a file: {path}", error_code="FILE_NOT_FOUND")
        basename = os.path.basename(resolved)
        if self._is_sensitive(basename):
            return ToolResult.fail(
                error=f"reading sensitive file {basename!r} is not allowed", error_code="SENSITIVE_FILE",
            )
        try:
            size = os.path.getsize(resolved)
        except FileNotFoundError:
            return ToolResult.fail(error=f"file not found: {path}", error_code="FILE_NOT_FOUND")
        if size > self.max_file_size:
            return ToolResult.fail(
                error=f"file too large ({size} bytes > {self.max_file_size} bytes)", error_code="FILE_TOO_LARGE",
            )
        try:
            with open(resolved, encoding=encoding) as fh:
                lines = fh.readlines()
        except UnicodeDecodeError as exc:
            return ToolResult.fail(
                error=f"cannot decode file with {encoding!r}: {exc}", error_code="FILESYSTEM_ERROR",
            )
        truncated = len(lines) > max_lines
        content = "".join(lines[:max_lines])
        return ToolResult.ok(
            data={
                "path": path,
                "content": content,
                "line_count": len(lines),
                "truncated": truncated,
            },
            text=content,
        )

    def _list(self, arguments: dict) -> ToolResult:
        path = arguments.get("path")
        recursive = bool(arguments.get("recursive", False))
        max_depth = int(arguments.get("max_depth", 3))
        resolved = self._resolve_path(path)
        if not os.path.isdir(resolved):
            return ToolResult.fail(error=f"not a directory: {path}", error_code="DIRECTORY_NOT_FOUND")

        entries: list[dict] = []
        root = os.path.normcase(os.path.realpath(self.allowed_root))

        def walk(directory: str, depth: int) -> None:
            for name in sorted(os.listdir(directory)):
                full = os.path.join(directory, name)
                # Defend against symlink escapes: resolve the real path and
                # confirm it stays inside the allowed root. Skip entries that
                # don't (prevents listing files/dirs outside the jail).
                real_full = os.path.realpath(full)
                if os.path.commonpath([root, os.path.normcase(real_full)]) != root:
                    continue
                is_dir = os.path.isdir(full)
                entries.append(
                    {
                        "name": name,
                        "path": os.path.relpath(real_full, self.allowed_root),
                        "is_dir": is_dir,
                        "size_bytes": os.path.getsize(real_full) if not is_dir else None,
                    }
                )
                if is_dir and recursive and depth < max_depth:
                    walk(real_full, depth + 1)

        walk(resolved, 0)
        names = [e.get("name") for e in entries if isinstance(e, dict)]
        return ToolResult.ok(
            data={"path": path, "entries": entries},
            text=f"{len(entries)} entries{_examples_summary(names)}",
        )

    def _search(self, arguments: dict) -> ToolResult:
        path = arguments.get("path")
        pattern = arguments.get("pattern")
        max_results = int(arguments.get("max_results", 50))
        resolved = self._resolve_path(path)
        if not os.path.isdir(resolved):
            return ToolResult.fail(error=f"not a directory: {path}", error_code="DIRECTORY_NOT_FOUND")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return ToolResult.fail(error=f"invalid search pattern: {exc}", error_code="FILESYSTEM_ERROR")

        root = os.path.normcase(os.path.realpath(self.allowed_root))
        matches: list[dict] = []
        for dirpath, _dirs, files in os.walk(resolved):
            for fname in files:
                if self._is_sensitive(fname):
                    continue
                full = os.path.join(dirpath, fname)
                # Resolve symlinks and verify containment before opening.
                real_full = os.path.realpath(full)
                if os.path.commonpath([root, os.path.normcase(real_full)]) != root:
                    continue
                if not os.path.isfile(real_full):
                    continue
                try:
                    if os.path.getsize(real_full) > self.max_file_size:
                        continue
                    with open(real_full, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if compiled.search(line):
                                matches.append(
                                    {
                                        "file": os.path.relpath(real_full, self.allowed_root),
                                        "line": lineno,
                                        "content": line.rstrip("\n")[:200],
                                    }
                                )
                                if len(matches) >= max_results:
                                    return ToolResult.ok(
                                        data={"path": path, "pattern": pattern, "matches": matches},
                                        text=f"{len(matches)} match(es)"
                                        + _examples_summary([m.get('file') for m in matches if isinstance(m, dict)]),
                                    )
                except OSError:
                    continue
        return ToolResult.ok(
            data={"path": path, "pattern": pattern, "matches": matches},
            text=f"{len(matches)} match(es)"
            + _examples_summary([m.get("file") for m in matches if isinstance(m, dict)]),
        )

    def _write(self, arguments: dict) -> ToolResult:
        path = arguments.get("path")
        content = arguments.get("content", "")
        encoding = arguments.get("encoding", "utf-8")
        overwrite = bool(arguments.get("overwrite", False))
        resolved = self._resolve_path(path)
        if self._is_sensitive(os.path.basename(resolved)):
            return ToolResult.fail(
                error=f"writing sensitive file {os.path.basename(resolved)!r} is not allowed",
                error_code="SENSITIVE_FILE",
            )
        if os.path.exists(resolved) and not overwrite:
            return ToolResult.fail(
                error=f"refusing to overwrite existing file: {path}", error_code="FILE_EXISTS",
            )
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        mode = "w" if overwrite else "x"
        with open(resolved, mode, encoding=encoding) as fh:
            fh.write(content)
        return ToolResult.ok(
            data={"path": path, "size_bytes": len(content.encode(encoding))},
            text=f"wrote {len(content.encode(encoding))} bytes to {path}",
        )
