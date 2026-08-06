"""Pydantic schemas for the cross-device sync API."""

import datetime
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_EMPTY_PAYLOAD: dict[str, list[Any]] = {
    "assets": [],
    "budgets": [],
    "transactions": [],
}
DEFAULT_SYNC_PAYLOAD = json.dumps(DEFAULT_EMPTY_PAYLOAD)


class SyncPayload(BaseModel):
    """Schema representing the sync data payload."""

    payload: str = Field(
        ...,
        max_length=100000,
        description="JSON-encoded string representing sync state.",
    )
    version: int = Field(
        ...,
        description="Monotonically increasing version for optimistic concurrency.",
    )

    @field_validator("payload")
    @classmethod
    def validate_json_payload(cls, v: str) -> str:
        """Verify that payload is a valid JSON string."""
        try:
            json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError("Payload must be a valid JSON-encoded string.") from e
        return v


class SyncItemSchema(BaseModel):
    """Schema representing a single syncable item."""

    id: str = Field(..., description="Unique client-generated ID (typically UUID).")
    type: str = Field(..., description="Type of the record (e.g. 'account', 'transaction').")
    data: dict[str, Any] = Field(..., description="Arbitrary JSON data payload.")
    updated_at: datetime.datetime = Field(
        ..., description="Timestamp indicating when the client last modified the item."
    )
    deleted: bool = Field(False, description="Flag indicating if the item has been soft-deleted.")

    model_config = ConfigDict(from_attributes=True)


class SyncRequest(BaseModel):
    """Schema for incoming sync requests."""

    since: datetime.datetime | None = Field(
        None, description="The client's last sync point. Returns changes modified after this."
    )
    changes: list[SyncItemSchema] = Field(
        default_factory=list,
        max_length=500,
        description="A list of locally changed items to upload.",
    )


class SyncResponse(BaseModel):
    """Schema for outgoing sync responses."""

    sync_point: datetime.datetime = Field(
        ..., description="The server's current timestamp to use as 'since' in the next sync."
    )
    changes: list[SyncItemSchema] = Field(
        ..., description="A list of items modified since the client's last sync point."
    )
