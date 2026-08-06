"""API router for cross-device synchronization."""

import datetime
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wealthdock_server.api.deps import get_current_user
from wealthdock_server.db.models import SyncRecord, SyncState, User
from wealthdock_server.db.session import get_db
from wealthdock_server.schemas.sync import (
    DEFAULT_SYNC_PAYLOAD,
    SyncItemSchema,
    SyncPayload,
    SyncRequest,
    SyncResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])

PAGE_LIMIT = 100
CLAMP_TOLERANCE_MINUTES = 5


async def process_changes(
    payload_changes: list[SyncItemSchema],
    current_user_id: Any,
    db: AsyncSession,
    server_sync_point: datetime.datetime,
) -> None:
    """Process incoming changes from the client using Last-Write-Wins (LWW) resolution.

    Ensures client-side clock skew is clamped, resolves tie-breaks, and saves records.
    """
    if not payload_changes:
        return

    # Fetch existing records in bulk to resolve N+1 queries
    ids = [change.id for change in payload_changes]
    stmt = select(SyncRecord).where(
        SyncRecord.user_id == current_user_id,
        SyncRecord.id.in_(ids),
    )
    result = await db.execute(stmt)
    existing = {record.id: record for record in result.scalars()}

    max_allowed_time = server_sync_point + datetime.timedelta(minutes=CLAMP_TOLERANCE_MINUTES)

    for change in payload_changes:
        # Normalize incoming client timestamp
        incoming_updated_at = change.updated_at
        if incoming_updated_at.tzinfo is None:
            incoming_updated_at = incoming_updated_at.replace(tzinfo=datetime.UTC)
        else:
            incoming_updated_at = incoming_updated_at.astimezone(datetime.UTC)

        # Clamp skewed clocks to server time + tolerance
        if incoming_updated_at > max_allowed_time:
            logger.warning(
                "Client sync record %s timestamp %s is too far in future. "
                "Clamping to server time %s.",
                change.id,
                incoming_updated_at,
                server_sync_point,
            )
            incoming_updated_at = server_sync_point

        db_record = existing.get(change.id)
        if db_record is None:
            new_record = SyncRecord(
                id=change.id,
                user_id=current_user_id,
                type=change.type,
                data=change.data,
                updated_at=incoming_updated_at,
                server_updated_at=server_sync_point,
                deleted=change.deleted,
            )
            db.add(new_record)
        else:
            db_updated_at = db_record.updated_at
            if db_updated_at.tzinfo is None:
                db_updated_at = db_updated_at.replace(tzinfo=datetime.UTC)
            else:
                db_updated_at = db_updated_at.astimezone(datetime.UTC)

            # Last-Write-Wins with tie-break on equality (incoming replaces stored)
            if incoming_updated_at >= db_updated_at:
                db_record.type = change.type
                db_record.data = change.data
                db_record.updated_at = incoming_updated_at
                db_record.deleted = change.deleted
                db_record.server_updated_at = server_sync_point


@router.get("", response_model=SyncPayload)
async def get_sync_state(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SyncPayload:
    """Retrieve the user's synced assets and configurations."""
    result = await db.execute(select(SyncState).where(SyncState.user_id == current_user.id))
    sync_state = result.scalar_one_or_none()
    if not sync_state:
        return SyncPayload(payload=DEFAULT_SYNC_PAYLOAD, version=0)
    return SyncPayload(payload=sync_state.payload, version=sync_state.version)


@router.post("", response_model=None)
async def sync(
    payload_in: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Any:
    """Synchronize user data supporting whole-state and per-record sync."""
    if "payload" in payload_in and "version" in payload_in:
        sync_payload = SyncPayload.model_validate(payload_in)
        result = await db.execute(
            select(SyncState).where(SyncState.user_id == current_user.id).with_for_update()
        )
        sync_state = result.scalar_one_or_none()

        if not sync_state:
            if sync_payload.version != 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="State conflict: version mismatch. No state found, expected version 0.",
                )
            sync_state = SyncState(user_id=current_user.id, payload=sync_payload.payload, version=1)
            db.add(sync_state)
            try:
                await db.commit()
            except IntegrityError as e:
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="State conflict: concurrent modification during initialization.",
                ) from e
        else:
            if sync_state.version != sync_payload.version:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="State conflict: version mismatch. Please fetch latest state and merge.",
                )
            sync_state.payload = sync_payload.payload
            sync_state.version += 1
            await db.commit()

        await db.refresh(sync_state)
        return SyncPayload(payload=sync_state.payload, version=sync_state.version)

    # Per-record LWW sync
    sync_req = SyncRequest.model_validate(payload_in)
    server_sync_point = datetime.datetime.now(datetime.UTC)

    if sync_req.changes:
        try:
            await process_changes(sync_req.changes, current_user.id, db, server_sync_point)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            await process_changes(sync_req.changes, current_user.id, db, server_sync_point)
            await db.commit()

    if sync_req.since is not None:
        since_time = sync_req.since
        if since_time.tzinfo is None:
            since_time = since_time.replace(tzinfo=datetime.UTC)
        else:
            since_time = since_time.astimezone(datetime.UTC)

        stmt_pull = select(SyncRecord).where(
            SyncRecord.user_id == current_user.id,
            SyncRecord.server_updated_at > since_time,
        )
    else:
        stmt_pull = select(SyncRecord).where(SyncRecord.user_id == current_user.id)

    stmt_pull = stmt_pull.order_by(SyncRecord.server_updated_at.asc()).limit(PAGE_LIMIT)

    result_pull = await db.execute(stmt_pull)
    db_changes = result_pull.scalars().all()

    if len(db_changes) == PAGE_LIMIT:
        last_item_time = db_changes[-1].server_updated_at
        if last_item_time.tzinfo is None:
            last_item_time = last_item_time.replace(tzinfo=datetime.UTC)
        server_sync_point = last_item_time

    changes_to_return: list[SyncItemSchema] = []
    for item in db_changes:
        item_updated_at = item.updated_at
        if item_updated_at.tzinfo is None:
            item_updated_at = item_updated_at.replace(tzinfo=datetime.UTC)

        changes_to_return.append(
            SyncItemSchema(
                id=item.id,
                type=item.type,
                data=item.data,
                updated_at=item_updated_at,
                deleted=item.deleted,
            )
        )

    return SyncResponse(sync_point=server_sync_point, changes=changes_to_return)
