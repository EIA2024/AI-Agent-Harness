"""T52 — terminal sanitizer + secret redactor security tests."""

from __future__ import annotations

from personal_ai_os.cli.output.jsonl import JSONLWriter
from personal_ai_os.cli.sanitize import (
    is_secret_key,
    redact_secrets,
    sanitize_text,
    strip_control_sequences,
    truncate_long_lines,
)
from personal_ai_os.cli.tui.render import transcript_lines


def test_osc_title_injection_stripped():
    payload = "hello \x1b]0;EVIL TITLE\x07world"
    assert strip_control_sequences(payload) == "hello world"


def test_osc_with_esc_backslash_terminator():
    payload = "a\x1b]2;cmd.exe\x1b\\b"
    assert strip_control_sequences(payload) == "ab"


def test_ansi_cursor_move_stripped():
    payload = "one\x1b[2K\x1b[1;1Htwo"
    assert strip_control_sequences(payload) == "onetwo"


def test_ansi_color_sequence_stripped():
    payload = "\x1b[31;1mred\x1b[0m"
    assert strip_control_sequences(payload) == "red"


def test_stray_control_characters_stripped_but_newline_kept():
    payload = "line1\x00\x01\x02line2\nline3"
    cleaned = strip_control_sequences(payload)
    assert "\x00" not in cleaned and "\x01" not in cleaned
    assert "\n" in cleaned
    assert "line1" in cleaned and "line2" in cleaned


def test_redact_secrets_masks_sensitive_keys():
    args = {
        "api_key": "sk-123",
        "password": "hunter2",
        "url": "https://example.com",
        "nested": {"token": "t0k3n", "safe": 1},
    }
    redacted = redact_secrets(args)
    assert redacted["api_key"] == "***"
    assert redacted["password"] == "***"
    assert redacted["url"] == "https://example.com"
    assert redacted["nested"] == {"token": "***", "safe": 1}


def test_secret_key_matches_variants():
    for key in ("api_key", "apiKey", "X-API-Key", "token", "password", "secret"):
        assert is_secret_key(key), key
    for key in ("path", "url", "content", "to"):
        assert not is_secret_key(key), key


def test_jsonl_emits_sanitized_lines():
    from personal_ai_os.cli.api.sse import SSEDecoder
    from personal_ai_os.cli.domain.normalizer import Normalizer
    from personal_ai_os.cli.domain.reducer import reduce
    from personal_ai_os.cli.domain.state import initial_state

    body = (
        "event: run.started\n"
        'data: {"run_id": "r1"}\n'
        "\n"
        "event: text.delta\n"
        'data: {"text": "clean \\u001b]0;HACK\\u0007answer"}\n'
        "\n"
        "event: run.completed\n"
        'data: {"run_id": "r1", "status": "completed"}\n'
        "\n"
    )
    out: list[str] = []
    writer = JSONLWriter(write=out.append)
    decoder = SSEDecoder()
    state = initial_state()
    normalizer = Normalizer()
    for line in body.splitlines(keepends=False):
        for ev in decoder.feed_line(line):
            event = normalizer.normalize(ev, run_id="r1")
            reduce(state, event)
            writer.emit(event, state)
    text_line = next(line for line in out if '"assistant.delta"' in line)
    assert "\x1b" not in text_line
    assert "clean answer" in text_line


def test_tui_render_sanitizes_malicious_tool_name():
    from personal_ai_os.cli.domain.events import UIEventType, ui_event
    from personal_ai_os.cli.domain.reducer import reduce
    from personal_ai_os.cli.domain.state import initial_state

    state = initial_state()
    reduce(
        state,
        ui_event(
            UIEventType.TOOL_REQUESTED,
            {"tool_call_id": "t1", "tool_name": "evil\x1b[31m\x1b]0;x\x07tool"},
        ),
    )
    lines = transcript_lines(state)
    joined = "\n".join(lines)
    assert "\x1b" not in joined
    assert "evil" in joined


def test_bidi_override_characters_stripped():
    assert strip_control_sequences("abc\u202eefg") == "abcefg"


def test_truncate_long_lines_breaks_unbroken_runs():
    long = "x" * 30_000
    truncated = truncate_long_lines(long, max_line=500)
    assert len(truncated.replace("\n", "")) == 30_000  # content preserved, just wrapped
    assert "\n" in truncated


def test_sanitize_text_applies_both():
    payload = "\x1b[31m" + ("y" * 15_000)
    cleaned = sanitize_text(payload)
    assert "\x1b" not in cleaned


def test_redact_secrets_recurses_lists():
    args = {"headers": [{"Authorization": "Bearer sk-xyz", "Host": "x.com"}]}
    redacted = redact_secrets(args)
    assert redacted["headers"][0]["Authorization"] == "***"
    assert redacted["headers"][0]["Host"] == "x.com"


def test_secret_key_matches_more_variants():
    for key in ("access_token", "client_secret", "passphrase", "session_id", "bearer"):
        assert is_secret_key(key), key


def test_rich_table_output_is_sanitized(capsys):
    import sys

    from personal_ai_os.cli.commands.table_output import emit

    emit(
        [{"name": "evil\x1b]0;HACK\x07tool", "desc": "desc"}],
        columns=[("NAME", "name"), ("DESC", "desc")],
        stdout=sys.stdout,
    )
    captured = capsys.readouterr().out
    assert "\x1b" not in captured
    assert "evil" in captured
