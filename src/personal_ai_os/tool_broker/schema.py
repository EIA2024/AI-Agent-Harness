"""JSON Schema argument validation for tool inputs.

Wraps the ``jsonschema`` library (Draft 2020-12) so callers get a single,
detailed ``ValueError`` describing every offending argument path.
"""

from __future__ import annotations

from jsonschema import Draft202012Validator

_MAX_REPORTED_ERRORS = 10


def validate_arguments(input_schema: dict, arguments: dict) -> None:
    """Validate ``arguments`` against ``input_schema``.

    Raises ``ValueError`` with a human-readable, detail-heavy message listing
    each failing path (e.g. ``path: 'foo' is not of type 'string'``).
    """
    validator = Draft202012Validator(input_schema)
    # Sort by error path so the message is stable and the root-most error first.
    errors = sorted(validator.iter_errors(arguments), key=lambda e: [str(p) for p in e.path])
    if not errors:
        return

    parts: list[str] = []
    for error in errors[:_MAX_REPORTED_ERRORS]:
        location = "/".join(str(p) for p in error.path) or "<root>"
        parts.append(f"{location}: {error.message}")
    if len(errors) > _MAX_REPORTED_ERRORS:
        parts.append(f"... and {len(errors) - _MAX_REPORTED_ERRORS} more error(s)")
    raise ValueError("Invalid arguments: " + "; ".join(parts))
