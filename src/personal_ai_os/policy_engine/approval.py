"""Approval engine — HITL approval requests, receipts and argument-hash binding.

Security model (dept 04 §4):

* An approval is bound to the *exact* arguments via ``argument_hash``.
* ``verify()`` is called right before execution: it checks the tool name and
  re-hashes the actual arguments, so "approved A, executed B" is impossible.
* Persistence lives in the ``approvals`` table (``db.models.Approval``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from ..common.models import ApprovalReceipt, ApprovalRequest
from ..common.utils import argument_hash as compute_argument_hash
from ..common.utils import ensure_aware, utc_now
from ..db.models import Approval
from ..db.session import session_scope

_RESOLVE_STATUS = {
    "approved": "approved",
    "approved_with_edits": "edited",
    "rejected": "rejected",
}


class ApprovalEngine:
    """Create, resolve and verify human-in-the-loop approvals."""

    # -- creation ----------------------------------------------------------

    async def create_request(
        self,
        *,
        run_id: UUID,
        session_id: UUID | None,
        owner_id: UUID,
        action_summary: str,
        tool_name: str,
        arguments_preview: dict,
        risk_level: int,
        risk_reason: str,
        requires_auth_method: str | None = None,
        expires_at: datetime | None = None,
        argument_hash: str | None = None,  # noqa: A002 - kw name mirrors common.utils
    ) -> ApprovalRequest:
        """Persist a new pending approval request and return the domain object."""
        hash_value = argument_hash or compute_argument_hash(arguments_preview)
        row = Approval(
            id=uuid4(),
            run_id=run_id,
            session_id=session_id,
            owner_id=owner_id,
            action_summary=action_summary,
            tool_name=tool_name,
            arguments_preview=arguments_preview or {},
            risk_level=risk_level,
            risk_reason=risk_reason or "",
            argument_hash=hash_value,
            requires_auth_method=requires_auth_method,
            status="pending",
            expires_at=expires_at,
            created_at=utc_now(),
        )
        async with session_scope() as session:
            session.add(row)
            await session.flush()
            return _to_request(row)

    async def get_request(self, approval_id: UUID) -> ApprovalRequest | None:
        """Return the approval (auto-expiring it if past ``expires_at``)."""
        async with session_scope() as session:
            row = await session.get(Approval, approval_id)
            if row is None:
                return None
            if row.status == "pending" and row.expires_at is not None and utc_now() > ensure_aware(row.expires_at):
                row.status = "expired"
                await session.flush()
            return _to_request(row)

    # -- resolution --------------------------------------------------------

    async def resolve(
        self,
        approval_id: UUID,
        *,
        decision: str,
        approved_by: UUID,
        edited_arguments: dict | None = None,
    ) -> ApprovalReceipt:
        """Resolve a pending approval and produce an :class:`ApprovalReceipt`."""
        if decision not in _RESOLVE_STATUS:
            raise ValueError(f"Unknown approval decision: {decision!r}")

        async with session_scope() as session:
            row = await session.get(Approval, approval_id)
            if row is None:
                raise ValueError(f"Approval {approval_id} not found")
            if row.status != "pending":
                raise ValueError(f"Approval {approval_id} already {row.status}")
            if row.expires_at is not None and utc_now() > ensure_aware(row.expires_at):
                row.status = "expired"
                await session.flush()
                raise ValueError(f"Approval {approval_id} has expired")

            row.status = _RESOLVE_STATUS[decision]
            row.approved_by = approved_by

            bound_args = edited_arguments if edited_arguments is not None else row.arguments_preview or {}
            bound_hash = compute_argument_hash(bound_args)
            # P0-003: persist the bound values in the SAME transaction, otherwise
            # verify_approval() later reads the original hash and rejects the
            # edited execution ("approved A, executed B" — but B was approved).
            if edited_arguments is not None:
                row.arguments_preview = edited_arguments
                row.argument_hash = bound_hash

            receipt = ApprovalReceipt(
                id=uuid4(),
                approval_id=approval_id,
                approved_by=approved_by,
                decision=decision,
                scope="single_action",
                tool_name=row.tool_name,
                argument_hash=bound_hash,
                expires_at=None,
            )
            await session.flush()
            return receipt

    # -- verification ------------------------------------------------------

    async def verify(self, receipt: ApprovalReceipt | None, tool_name: str, arguments: dict) -> bool:
        """Check a receipt before execution: exact tool + exact arguments.

        Returns False if the receipt was rejected, expired, for a different
        tool, or bound to different arguments.
        """
        if receipt is None:
            return False
        if receipt.decision not in ("approved", "approved_with_edits"):
            return False
        if receipt.expires_at is not None and utc_now() > ensure_aware(receipt.expires_at):
            return False
        if receipt.tool_name != tool_name:
            return False
        return receipt.argument_hash == compute_argument_hash(arguments)

    async def verify_approval(self, approval_id: UUID, tool_name: str, arguments: dict) -> bool:
        """Load a persisted approval by id and bind-check it against the exact
        tool + arguments about to execute.

        This is the execution-time guard: an approved request can only execute
        the exact arguments whose hash was bound at resolution time (preventing
        "approved A, executed B"). Non-approved statuses always fail closed.
        """
        async with session_scope() as session:
            row = await session.get(Approval, approval_id)
            if row is None:
                return False
            if row.status not in ("approved", "edited"):
                return False
            if row.expires_at is not None and utc_now() > ensure_aware(row.expires_at):
                return False
            if row.tool_name != tool_name:
                return False
            if row.argument_hash is None:
                # No hash bound at creation → bind now from the preview, so a
                # call is only valid if it matches the original request.
                return compute_argument_hash(arguments) == compute_argument_hash(row.arguments_preview or {})
            return compute_argument_hash(arguments) == row.argument_hash

    # -- status helpers ----------------------------------------------------

    async def get_status(self, approval_id: UUID) -> str | None:
        """Return the persisted status of an approval (for UI/tests)."""
        async with session_scope() as session:
            row = await session.get(Approval, approval_id)
            return row.status if row is not None else None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _to_request(row: Approval) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        run_id=row.run_id,
        session_id=row.session_id,
        owner_id=row.owner_id,
        action_summary=row.action_summary,
        tool_name=row.tool_name,
        arguments_preview=row.arguments_preview or {},
        risk_level=row.risk_level,
        risk_reason=row.risk_reason or "",
        requires_auth_method=row.requires_auth_method,
        expires_at=row.expires_at,
        status=row.status,
        created_at=row.created_at,
    )
