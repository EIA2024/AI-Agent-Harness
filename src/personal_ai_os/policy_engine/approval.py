"""Approval engine — HITL requests, atomic resolution and argument binding."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update

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


class ApprovalError(Exception):
    """Base for approval-resolution failures."""


class ApprovalNotFoundError(ApprovalError):
    pass


class ApprovalNotPendingError(ApprovalError):
    pass


class ApprovalExpiredError(ApprovalError):
    pass


class ApprovalInvalidDecisionError(ApprovalError):
    pass


class ApprovalAuthMethodRequiredError(ApprovalError):
    """The request requires a verifier that this service does not provide."""


class ApprovalEngine:
    """Create, resolve and verify human-in-the-loop approvals."""

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
        argument_hash: str | None = None,  # noqa: A002
    ) -> ApprovalRequest:
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
        async with session_scope() as session:
            row = await session.get(Approval, approval_id)
            if row is None:
                return None
            if (
                row.status == "pending"
                and row.expires_at is not None
                and utc_now() > ensure_aware(row.expires_at)
            ):
                row.status = "expired"
                await session.flush()
            return _to_request(row)

    async def resolve(
        self,
        approval_id: UUID,
        *,
        decision: str,
        approved_by: UUID,
        edited_arguments: dict | None = None,
    ) -> ApprovalReceipt:
        """Resolve exactly one pending row using an atomic pending->terminal CAS.

        A plain SELECT followed by ORM mutation allowed two concurrent approvers
        to both observe ``pending``. The conditional UPDATE makes the database
        pick one winner on both PostgreSQL and SQLite.
        """
        if decision not in _RESOLVE_STATUS:
            raise ApprovalInvalidDecisionError(f"Unknown approval decision: {decision!r}")

        async with session_scope() as session:
            row = (
                await session.execute(select(Approval).where(Approval.id == approval_id))
            ).scalar_one_or_none()
            if row is None:
                raise ApprovalNotFoundError(f"Approval {approval_id} not found")
            if row.status != "pending":
                if row.status == "expired":
                    raise ApprovalExpiredError(f"Approval {approval_id} has expired")
                raise ApprovalNotPendingError(
                    f"Approval {approval_id} already {row.status}"
                )
            if row.expires_at is not None and utc_now() > ensure_aware(row.expires_at):
                raise ApprovalExpiredError(f"Approval {approval_id} has expired")

            # R4 currently declares passkey/step-up requirements, but this codebase
            # has no verifier. Treat missing security infrastructure as DENY rather
            # than silently accepting API-key authentication as a substitute.
            if row.requires_auth_method:
                raise ApprovalAuthMethodRequiredError(
                    f"Approval {approval_id} requires {row.requires_auth_method!r}; "
                    "no verifier is wired, so resolution is refused"
                )

            bound_args = (
                edited_arguments
                if edited_arguments is not None
                else row.arguments_preview or {}
            )
            bound_hash = compute_argument_hash(bound_args)
            values: dict = {
                "status": _RESOLVE_STATUS[decision],
                "approved_by": approved_by,
            }
            if edited_arguments is not None:
                values["arguments_preview"] = edited_arguments
                values["argument_hash"] = bound_hash

            result = await session.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending")
                .values(**values)
            )
            if result.rowcount != 1:
                current = await session.get(Approval, approval_id)
                status = current.status if current is not None else "missing"
                raise ApprovalNotPendingError(
                    f"Approval {approval_id} concurrently resolved as {status}"
                )

            return ApprovalReceipt(
                id=uuid4(),
                approval_id=approval_id,
                approved_by=approved_by,
                decision=decision,
                scope="single_action",
                tool_name=row.tool_name,
                argument_hash=bound_hash,
                expires_at=None,
            )

    async def verify(
        self, receipt: ApprovalReceipt | None, tool_name: str, arguments: dict
    ) -> bool:
        if receipt is None:
            return False
        if receipt.decision not in ("approved", "approved_with_edits"):
            return False
        if receipt.expires_at is not None and utc_now() > ensure_aware(receipt.expires_at):
            return False
        if receipt.tool_name != tool_name:
            return False
        return receipt.argument_hash == compute_argument_hash(arguments)

    async def verify_approval(
        self, approval_id: UUID, tool_name: str, arguments: dict
    ) -> bool:
        async with session_scope() as session:
            row = await session.get(Approval, approval_id)
            if row is None:
                return False
            if row.status not in ("approved", "edited"):
                return False
            if row.requires_auth_method:
                # There is no step-up verifier in the current system. Even a DB
                # row manually flipped to approved must not cross the broker edge.
                return False
            if row.expires_at is not None and utc_now() > ensure_aware(row.expires_at):
                return False
            if row.tool_name != tool_name:
                return False
            expected = row.argument_hash or compute_argument_hash(
                row.arguments_preview or {}
            )
            return compute_argument_hash(arguments) == expected

    async def get_status(self, approval_id: UUID) -> str | None:
        async with session_scope() as session:
            row = await session.get(Approval, approval_id)
            return row.status if row is not None else None


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
