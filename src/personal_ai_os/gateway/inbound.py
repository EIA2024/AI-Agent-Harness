"""Inbound message normalization and routing-key helpers.

The gateway layer converts raw channel payloads into a single normalized
``InboundMessage`` and derives a stable routing key for session lookup.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from personal_ai_os.common.models import InboundMessage


def normalize_inbound(
    *,
    channel: str,
    sender_id: str,
    owner_id: UUID | str,
    conversation_key: str,
    text: str | None = None,
    metadata: dict | None = None,
) -> InboundMessage:
    """Build a normalized :class:`InboundMessage` from raw channel fields.

    ``owner_id`` may be given as a UUID or as a string (e.g. from a webhook
    payload) and is coerced to :class:`uuid.UUID`.
    """
    owner = owner_id if isinstance(owner_id, UUID) else UUID(str(owner_id))
    return InboundMessage(
        channel=channel,
        channel_account_id="",
        sender_id=sender_id,
        owner_id=owner,
        conversation_key=conversation_key,
        text=text,
        metadata=metadata or {},
    )


def session_key(*, channel: str, sender_id: str, conversation_key: str) -> str:
    """Deterministic routing key that buckets a channel conversation.

    The key is a SHA-256 digest of ``channel|sender_id|conversation_key`` so
    the same logical conversation always maps to the same bucket while keeping
    the stored key compact.
    """
    raw = f"{channel}|{sender_id}|{conversation_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
