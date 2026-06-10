from __future__ import annotations

from datetime import datetime
from enum import Enum

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator


class InsightType(str, Enum):
    STALE_DOCS = "stale_docs"
    DEPENDENCY_RISK = "dependency_risk"
    DUPLICATE_LOGIC = "duplicate_logic"
    OWNERSHIP_GAP = "ownership_gap"
    COMPLEXITY_SPIKE = "complexity_spike"
    BREAKING_CHANGE_RISK = "breaking_change_risk"


class InsightSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class RelationshipType(str, Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    EXTENDS = "extends"
    REFERENCES = "references"


class MongoDocument(BaseModel):
    id: ObjectId | None = Field(default=None, alias="_id")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="forbid",
        json_encoders={ObjectId: str},
    )

    @field_validator("id", mode="before")
    @classmethod
    def validate_object_id(cls, value: ObjectId | str | None) -> ObjectId | None:
        if value is None or isinstance(value, ObjectId):
            return value
        if ObjectId.is_valid(value):
            return ObjectId(value)
        raise ValueError("Invalid ObjectId")


class FileDocument(MongoDocument):
    repo_id: str
    path: str
    language: str
    size_bytes: int
    last_modified: datetime
    owner: str
    doc_coverage: float = Field(ge=0.0, le=1.0)
    max_complexity: int = Field(default=0, ge=0)
    indexed_at: datetime


class ChunkDocument(MongoDocument):
    file_id: ObjectId
    repo_id: str
    content: str
    chunk_index: int
    start_line: int
    end_line: int
    embedding: list[float]

    @field_validator("file_id", mode="before")
    @classmethod
    def validate_file_id(cls, value: ObjectId | str) -> ObjectId:
        if isinstance(value, ObjectId):
            return value
        if ObjectId.is_valid(value):
            return ObjectId(value)
        raise ValueError("Invalid file_id ObjectId")


class RelationshipDocument(MongoDocument):
    repo_id: str
    from_file: str
    to_file: str
    type: RelationshipType
    weight: int = Field(ge=1)


class InsightDocument(MongoDocument):
    repo_id: str
    type: InsightType
    severity: InsightSeverity
    title: str
    description: str
    affected_files: list[str]
    created_at: datetime
    resolved: bool
    snapshot_id: ObjectId

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def validate_snapshot_id(cls, value: ObjectId | str) -> ObjectId:
        if isinstance(value, ObjectId):
            return value
        if ObjectId.is_valid(value):
            return ObjectId(value)
        raise ValueError("Invalid snapshot_id ObjectId")


class SnapshotDocument(MongoDocument):
    repo_id: str
    commit_hash: str
    timestamp: datetime
    files_changed: list[str]
    insights_generated: int = Field(ge=0)


class RepoStatus(str, Enum):
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


class RepoDocument(MongoDocument):
    url: str
    name: str
    default_branch: str
    last_synced: datetime
    total_files: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    status: RepoStatus
