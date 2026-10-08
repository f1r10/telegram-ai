"""Minimal technical model used to prove the migration and persistence foundation."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from office_assistant.infrastructure.database.base import Base


class SystemMetadata(Base):
    """Small key/value table for technical application metadata."""

    __tablename__ = "system_metadata"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"


class UserStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    DISABLED = "DISABLED"


class User(Base):
    """Telegram identity and application access state."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('ADMIN', 'USER')", name="ck_users_role"),
        CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'REJECTED', 'DISABLED')",
            name="ck_users_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Asia/Baku")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AuditEvent(Base):
    """Security and future application mutation audit record."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_metadata: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FileObjectState(StrEnum):
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


class IngestionSessionStatus(StrEnum):
    OPEN = "OPEN"
    COMMITTED = "COMMITTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class IngestionItemType(StrEnum):
    FILE = "FILE"
    INSTRUCTION = "INSTRUCTION"


class IngestionJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DocumentProcessingStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEFERRED = "DEFERRED"
    FAILED = "FAILED"


class OutboxEventStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class KnowledgeIndexJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class KnowledgeIndexRecordStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FileObject(Base):
    """Physical uploaded bytes and their content identity."""

    __tablename__ = "file_objects"
    __table_args__ = (
        CheckConstraint("state IN ('STAGED', 'ACTIVE', 'DELETED')", name="ck_file_objects_state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestionSession(Base):
    """A user's pending or committed save package."""

    __tablename__ = "ingestion_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'COMMITTED', 'CANCELLED', 'EXPIRED')",
            name="ck_ingestion_sessions_status",
        ),
        Index(
            "uq_ingestion_sessions_open_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestionSessionItem(Base):
    """Ordered file or instruction associated with a session."""

    __tablename__ = "ingestion_session_items"
    __table_args__ = (
        CheckConstraint("item_type IN ('FILE', 'INSTRUCTION')", name="ck_ingestion_items_type"),
        CheckConstraint(
            "(item_type = 'FILE' AND file_object_id IS NOT NULL) OR "
            "(item_type = 'INSTRUCTION' AND text_content IS NOT NULL)",
            name="ck_ingestion_items_payload",
        ),
        UniqueConstraint("session_id", "sequence_no", name="uq_ingestion_item_sequence"),
        UniqueConstraint(
            "telegram_chat_id", "telegram_message_id", name="uq_ingestion_telegram_message"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_object_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("file_objects.id", ondelete="RESTRICT"), nullable=True
    )
    submitted_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IngestionJob(Base):
    """Future processing job created at the /saxla commit boundary."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_ingestion_jobs_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_sessions.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class DocumentParseRun(Base):
    """Versioned normalized output metadata for one file in one ingestion job."""

    __tablename__ = "document_parse_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'DEFERRED', 'FAILED')",
            name="ck_document_parse_runs_status",
        ),
        Index("ix_document_parse_runs_job_file", "ingestion_job_id", "file_object_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    file_object_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("file_objects.id", ondelete="RESTRICT"), nullable=False
    )
    ingestion_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), nullable=False
    )
    session_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_session_items.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requires_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxEvent(Base):
    """Durable application event consumed by the Telegram notification loop."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_pending", "status", "available_at"),
        Index("ix_outbox_events_recipient", "recipient_telegram_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeIndexJob(Base):
    """Durable projection job for normalized parse results."""

    __tablename__ = "knowledge_index_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_knowledge_index_jobs_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ingestion_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collection_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class KnowledgeIndexRecord(Base):
    """Per-parse-run index state and Qdrant projection metadata."""

    __tablename__ = "knowledge_index_records"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_index_job_id", "parse_run_id", name="uq_knowledge_index_job_parse"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_knowledge_index_records_status",
        ),
        Index("ix_knowledge_index_records_parse_run", "parse_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    knowledge_index_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_index_jobs.id", ondelete="CASCADE"), nullable=False
    )
    parse_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_parse_runs.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(100), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AIConversation(Base):
    """Application-owned bounded conversation identity."""

    __tablename__ = "ai_conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "mode", name="uq_ai_conversations_user_mode"),
        Index("ix_ai_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AIMessage(Base):
    """Small application-owned message record; source payloads are not stored here."""

    __tablename__ = "ai_messages"
    __table_args__ = (
        Index("ix_ai_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AIMessageSource(Base):
    """Bounded citation-to-file mapping for secure follow-up delivery."""

    __tablename__ = "ai_message_sources"
    __table_args__ = (
        UniqueConstraint("message_id", "citation_index", name="uq_ai_message_source_citation"),
        Index("ix_ai_message_sources_expiry", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    citation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("file_objects.id", ondelete="RESTRICT"), nullable=True
    )
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    locator: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    external_title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebSearchCache(Base):
    """Bounded provider-neutral search results; final AI prose is never cached here."""

    __tablename__ = "web_search_cache"
    __table_args__ = (Index("ix_web_search_cache_expires", "expires_at"),)

    query_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    normalized_query: Mapped[str] = mapped_column(String(1000), nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    results: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIUsage(Base):
    """Provider usage telemetry without pricing assumptions."""

    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
        Index("ix_ai_usage_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    request_type: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extraction_run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("structured_extraction_runs.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceType(StrEnum):
    TELEGRAM_USER = "TELEGRAM_USER"
    INGESTION_SESSION = "INGESTION_SESSION"
    FILE = "FILE"
    DOCUMENT_PARSE = "DOCUMENT_PARSE"
    MANUAL_ADMIN = "MANUAL_ADMIN"
    FUTURE_AI_EXTRACTION = "FUTURE_AI_EXTRACTION"


class PriceKind(StrEnum):
    GENERAL = "GENERAL"
    CUSTOMER_SPECIFIC = "CUSTOMER_SPECIFIC"


class BusinessEventType(StrEnum):
    MEETING = "MEETING"
    PHONE_CALL = "PHONE_CALL"
    SERVICE_ACTIVITY = "SERVICE_ACTIVITY"
    DELIVERY = "DELIVERY"
    NOTE = "NOTE"
    AGREEMENT = "AGREEMENT"
    FOLLOW_UP = "FOLLOW_UP"
    OTHER = "OTHER"


class ProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ProposalDocumentRelation(StrEnum):
    PRIMARY = "PRIMARY"
    ATTACHMENT = "ATTACHMENT"
    EXPORT = "EXPORT"


class SourceReference(Base):
    """Bounded provenance pointer shared by structured business facts."""

    __tablename__ = "source_references"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('TELEGRAM_USER', 'INGESTION_SESSION', 'FILE', "
            "'DOCUMENT_PARSE', 'MANUAL_ADMIN', 'FUTURE_AI_EXTRACTION')",
            name="ck_source_references_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("file_objects.id", ondelete="RESTRICT"), nullable=True
    )
    ingestion_session_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_sessions.id", ondelete="RESTRICT"), nullable=True
    )
    ingestion_session_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_session_items.id", ondelete="RESTRICT"), nullable=True
    )
    document_parse_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("document_parse_runs.id", ondelete="RESTRICT"), nullable=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    locator: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    canonical_code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class CustomerAlias(Base):
    __tablename__ = "customer_aliases"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )


class ProductPrice(Base):
    __tablename__ = "product_prices"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_product_prices_amount"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_product_prices_currency"),
        CheckConstraint(
            "price_kind IN ('GENERAL', 'CUSTOMER_SPECIFIC')", name="ck_product_prices_kind"
        ),
        CheckConstraint(
            "(price_kind = 'GENERAL' AND customer_id IS NULL) OR "
            "(price_kind = 'CUSTOMER_SPECIFIC' AND customer_id IS NOT NULL)",
            name="ck_product_prices_scope",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > effective_at",
            name="ck_product_prices_validity",
        ),
        Index(
            "ix_product_prices_resolution",
            "product_id",
            "customer_id",
            "effective_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("product_prices.id", ondelete="RESTRICT"), nullable=True
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_positive_amount"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_payments_currency"),
        Index("ix_payments_customer_paid", "customer_id", "paid_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class BusinessEvent(Base):
    __tablename__ = "business_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('MEETING', 'PHONE_CALL', 'SERVICE_ACTIVITY', 'DELIVERY', "
            "'NOTE', 'AGREEMENT', 'FOLLOW_UP', 'OTHER')",
            name="ck_business_events_type",
        ),
        Index("ix_business_events_customer_occurred", "customer_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'SENT', 'ACCEPTED', 'REJECTED', 'EXPIRED', 'CANCELLED')",
            name="ck_proposals_status",
        ),
        Index("ix_proposals_customer_date", "customer_id", "proposal_date"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    proposal_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proposal_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class ProposalDocument(Base):
    __tablename__ = "proposal_documents"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('PRIMARY', 'ATTACHMENT', 'EXPORT')",
            name="ck_proposal_documents_relation",
        ),
        UniqueConstraint("proposal_id", "file_id", "relation_type", name="uq_proposal_document"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("proposals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("file_objects.id", ondelete="RESTRICT"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProposalItem(Base):
    __tablename__ = "proposal_items"
    __table_args__ = (
        CheckConstraint(
            "product_id IS NOT NULL OR raw_product_name IS NOT NULL",
            name="ck_proposal_items_product_reference",
        ),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_proposal_items_quantity"),
        CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0", name="ck_proposal_items_unit_price"
        ),
        CheckConstraint(
            "line_total IS NULL OR line_total >= 0", name="ck_proposal_items_line_total"
        ),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_proposal_items_currency",
        ),
        CheckConstraint("position > 0", name="ck_proposal_items_position"),
        UniqueConstraint("proposal_id", "position", name="uq_proposal_item_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("proposals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    raw_product_name: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    source_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_references.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class ExtractionJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExtractionBatchStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class CandidateStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


class ExtractionReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPLYING = "APPLYING"
    PARTIAL = "PARTIAL"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class StructuredExtractionJob(Base):
    __tablename__ = "structured_extraction_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_extraction_jobs_status",
        ),
        Index("ix_extraction_jobs_claim", "status", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ingestion_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingestion_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StructuredExtractionRun(Base):
    __tablename__ = "structured_extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_extraction_runs_status",
        ),
        CheckConstraint(
            "review_status IN ('PENDING', 'APPLYING', 'PARTIAL', 'APPLIED', 'REJECTED', 'EXPIRED')",
            name="ck_extraction_runs_review_status",
        ),
        UniqueConstraint("job_id", name="uq_extraction_runs_job"),
        Index("ix_extraction_runs_session_created", "ingestion_session_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("structured_extraction_jobs.id", ondelete="CASCADE"), nullable=False
    )
    ingestion_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingestion_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deferred_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class StructuredExtractionBatch(Base):
    __tablename__ = "structured_extraction_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_extraction_batches_status",
        ),
        UniqueConstraint("extraction_run_id", "batch_number", name="uq_extraction_batch_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    extraction_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("structured_extraction_runs.id", ondelete="CASCADE"), nullable=False
    )
    batch_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_range: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StructuredExtractionCandidate(Base):
    __tablename__ = "structured_extraction_candidates"
    __table_args__ = (
        CheckConstraint(
            "candidate_type IN ('PRODUCT', 'PRODUCT_ALIAS', 'CUSTOMER', 'CUSTOMER_ALIAS', "
            "'PRICE', 'PAYMENT', 'BUSINESS_EVENT', 'PROPOSAL', 'PROPOSAL_ITEM')",
            name="ck_extraction_candidates_type",
        ),
        CheckConstraint(
            "status IN ('EXTRACTED', 'VALIDATED', 'NEEDS_REVIEW', 'CONFLICT', 'REJECTED', "
            "'APPROVED', 'APPLIED', 'FAILED', 'ALREADY_EXISTS')",
            name="ck_extraction_candidates_status",
        ),
        UniqueConstraint("extraction_run_id", "fingerprint", name="uq_extraction_candidate"),
        Index("ix_extraction_candidates_run_status", "extraction_run_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    extraction_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("structured_extraction_runs.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("structured_extraction_batches.id", ondelete="CASCADE"), nullable=False
    )
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    group_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    validation_errors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    resolution_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    applied_entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    applied_entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReminderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    SENT = "SENT"
    CANCELLED = "CANCELLED"


class Reminder(Base):
    """User-confirmed natural-language reminder (SPEC §5, §10).

    ``due_at_utc`` is the authoritative UTC instant; ``timezone`` keeps the
    user's IANA name so display stays correct. ``recurrence_rule`` and
    ``next_run_at`` are reserved and unused in V1.
    """

    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'PAUSED', 'SENT', 'CANCELLED')",
            name="ck_reminders_status",
        ),
        Index("ix_reminders_due_claim", "status", "due_at_utc"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    due_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ReminderDeliveryStatus(StrEnum):
    CLAIMED = "CLAIMED"
    SENDING = "SENDING"
    SENT = "SENT"
    RETRY_PENDING = "RETRY_PENDING"
    FAILED = "FAILED"


class ReminderDelivery(Base):
    """Delivery ledger row: one attempt sequence per reminder occurrence.

    At-least-once policy: ``claimed -> sending -> sent``; failures cycle
    through ``retry_pending`` and terminate in ``failed`` after max attempts.
    """

    __tablename__ = "reminder_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CLAIMED', 'SENDING', 'SENT', 'RETRY_PENDING', 'FAILED')",
            name="ck_reminder_deliveries_status",
        ),
        UniqueConstraint("reminder_id", "scheduled_for", name="uq_reminder_delivery"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    reminder_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImageAssetOrigin(StrEnum):
    STANDALONE_UPLOAD = "STANDALONE_UPLOAD"
    PDF_EMBEDDED = "PDF_EMBEDDED"
    DOCX_EMBEDDED = "DOCX_EMBEDDED"
    XLSX_EMBEDDED = "XLSX_EMBEDDED"


class ImageAssetStorageKind(StrEnum):
    LOCAL = "LOCAL"


class ImageAsset(Base):
    """Distinct image content, keyed by its own bytes and stored exactly once (Master Spec §23.4).

    Provenance is deliberately absent here: one logo seen fifty times is one row, and every
    sighting is an ``image_asset_occurrence``. Bytes live in their own namespace, never in the
    ``blobs/`` root that holds original uploads (§23.3).
    """

    __tablename__ = "image_assets"
    __table_args__ = (
        CheckConstraint("storage_kind IN ('LOCAL')", name="ck_image_assets_storage_kind"),
        CheckConstraint("byte_size > 0", name="ck_image_assets_byte_size"),
        UniqueConstraint("content_sha256", name="uq_image_assets_content_sha256"),
        UniqueConstraint("storage_key", name="uq_image_assets_storage_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    # Null when the format's header does not carry a size; never estimated (Master Spec §23.5).
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ImageAssetStorageKind.LOCAL
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImageAssetOccurrence(Base):
    """Where one exact image content was seen, with the locator that says so (§23.4, §23.5).

    A locator is stored as given and hashed only to make the uniqueness check indexable, so a
    reprocessed run cannot inflate the provenance count while a document that legitimately holds
    the same bytes in fifty places keeps fifty rows.
    """

    __tablename__ = "image_asset_occurrences"
    __table_args__ = (
        CheckConstraint(
            "origin_type IN ('STANDALONE_UPLOAD', 'PDF_EMBEDDED', 'DOCX_EMBEDDED', "
            "'XLSX_EMBEDDED')",
            name="ck_image_asset_occurrences_origin",
        ),
        UniqueConstraint(
            "image_asset_id",
            "origin_type",
            "source_file_id",
            "source_parse_run_id",
            "locator_sha256",
            name="uq_image_asset_occurrence",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    image_asset_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("image_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # The parent document stays the authoritative source artifact; a derived image never becomes
    # a fake user upload of its own (§23.3), so deleting that document takes its images with it.
    source_file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("file_objects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_parse_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("document_parse_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    locator_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
