"""Filesystem connector: read / list / search / write within a jailed root.

Every path is resolved with ``os.path.realpath`` and must stay inside the
``allowed_root`` configured at construction (blocks ``../`` escape). Sensitive
files (``.env``, ``*.pem``, ``*id_rsa*``, ``*credentials*``, ``*.key``) are
never read or searched. Reads refuse files larger than ``max_file_size``.
"""

from __future__ import annotations

import asyncio
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
    # P1-005: broaden the secret-boundary denylist (complementary to the root
    # allowlist — not a replacement for a proper workspace allowlist).
    re.compile(r"^\.git(ignore)?$", re.IGNORECASE),
    re.compile(r"^\.ssh($|/)", re.IGNORECASE),
    re.compile(r"\.p12$|\.pfx$|\.jks$", re.IGNORECASE),
    re.compile(r"^secret", re.IGNORECASE),
    re.compile(r"^\.npmrc$|^\.pypirc$|^\.netrc$", re.IGNORECASE),
    re.compile(r"\.kubeconfig$|^\.kube/", re.IGNORECASE),
    re.compile(r"^token", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"^\.aws($|/)", re.IGNORECASE),
    re.compile(r"service_account", re.IGNORECASE),
    re.compile(r"^\.dockercfg$|^\.docker/", re.IGNORECASE),
]

#: Hard ceiling for directory recursion depth (P1-002).
HARD_MAX_DEPTH = 12


def _examples_summary(items: list[str], *, limit: int = 8) -> str:
    """Human preview of the first few names, e.g. ``(e.g. src, docs, tests…)``."""
    shown = items[:limit]
    if not shown:
        return ""
    suffix = "…" if len(items) > limit else ""
    return f" (e.g. {', '.join(shown)}{suffix})"


class FilesystemConnector:
    """Exposes filesystem.read/list/search/write tools rooted at ``allowed_root``.

    The root is explicit and canonical (P1-004): when ``allowed_root`` is not
    given it must come from ``PERSONAL_AI_WORKSPACE_ROOT``; falling back to the
    process working directory would make the security boundary a side effect of
    the launch directory, so that fallback is refused.
    """

    connector_name = "filesystem"

    def __init__(
        self,
        *,
        allowed_root: str | None = None,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        if allowed_root is None:
            workspace = os.environ.get("PERSONAL_AI_WORKSPACE_ROOT")
            if not workspace:
                raise ValueError(
                    "filesystem connector requires an explicit root: pass "
                    "allowed_root or set PERSONAL_AI_WORKSPACE_ROOT "
                    "(refusing to derive the security boundary from the cwd, P1-004)"
                )
            allowed_root = workspace
        self.allowed_root = os.path.realpath(allowed_root)
        self.max_file_size = max_file_size
        self.max_depth = max(min(max_depth or DEFAULT_MAX_DEPTH, HARD_MAX_DEPTH), 1)

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
                        "mode": {"type": "string", "enum": ["literal", "regex"], "default": "literal"},
                        "max_files": {"type": "integer", "minimum": 1, "default": 5000},
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
                # P1-013: writes stay INSIDE the jailed root — a local sandboxed
                # side effect, not an external system write (which must be R3+).
                external_write=False,
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
                return await self._search(arguments)
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
        # P1-002: clamp requested depth to the configured AND hard ceiling so a
        # model can never request an unbounded recursive walk.
        max_depth = min(int(arguments.get("max_depth", 3)), self.max_depth, HARD_MAX_DEPTH)
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

    async def _search(self, arguments: dict) -> ToolResult:
        """Literal substring search by default; ``mode=regex`` is explicit.

        P1-003: the scan runs off the event loop (``asyncio.to_thread``) with a
        hard file-scan budget, so a large tree or a pathological regex cannot
        block the server or burn unbounded CPU (ReDoS).
        """
        path = arguments.get("path")
        pattern = str(arguments.get("pattern", ""))
        mode = str(arguments.get("mode", "literal")).lower()
        max_results = int(arguments.get("max_results", 50))
        max_files = int(arguments.get("max_files", 5000))
        resolved = self._resolve_path(path)
        if not os.path.isdir(resolved):
            return ToolResult.fail(error=f"not a directory: {path}", error_code="DIRECTORY_NOT_FOUND")
        if not pattern:
            return ToolResult.fail(error="empty search pattern", error_code="FILESYSTEM_ERROR")
        if mode not in ("literal", "regex"):
            return ToolResult.fail(error=f"invalid search mode: {mode!r} (literal|regex)", error_code="FILESYSTEM_ERROR")
        compiled = None
        if mode == "regex":
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                return ToolResult.fail(error=f"invalid regex: {exc}", error_code="FILESYSTEM_ERROR")

        root = os.path.normcase(os.path.realpath(self.allowed_root))

        def _scan() -> tuple[list[dict], bool]:
            matches: list[dict] = []
            files_scanned = 0
            for dirpath, _dirs, files in os.walk(resolved):
                for fname in files:
                    if files_scanned >= max_files:
                        return matches, True  # budget exhausted → tell the model
                    files_scanned += 1
                    if self._is_sensitive(fname):
                        continue
                    full = os.path.join(dirpath, fname)
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
                                hit = compiled.search(line) if mode == "regex" else pattern in line
                                if hit:
                                    matches.append(
                                        {
                                            "file": os.path.relpath(real_full, self.allowed_root),
                                            "line": lineno,
                                            "content": line.rstrip("\n")[:200],
                                        }
                                    )
                                    if len(matches) >= max_results:
                                        return matches, False
                    except OSError:
                        continue
            return matches, False

        matches, budget_hit = await asyncio.to_thread(_scan)
        text = f"{len(matches)} match(es)"
        if budget_hit:
            text += " (scan budget reached — narrow the path)"
        text += _examples_summary([m.get("file") for m in matches if isinstance(m, dict)])
        return ToolResult.ok(
            data={"path": path, "pattern": pattern, "mode": mode, "matches": matches},
            text=text,
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
