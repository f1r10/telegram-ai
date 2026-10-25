"""Administrator-managed first-search domains for external web evidence.

The existing system_metadata row persists an ordered, bounded domain list
without a schema change. Search still treats every website as untrusted evidence.
"""

import ipaddress
import json
import re
from typing import cast
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import (
    AuditEvent,
    SystemMetadata,
    User,
    UserRole,
    UserStatus,
)

PRIORITY_SITES_KEY = "web_search_priority_domains_v1"
MAX_PRIORITY_SITES = 10
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InvalidPrioritySite(ValueError):
    pass


class PrioritySitePermissionDenied(PermissionError):
    pass


def normalize_priority_domain(value: str) -> str:
    """Accept a hostname or root URL, not paths, arbitrary search operators or IPs."""

    cleaned = value.strip()
    if not cleaned or len(cleaned) > 300 or any(ch.isspace() for ch in cleaned):
        raise InvalidPrioritySite("Domen yazın, məsələn: knatank.az")
    if "://" not in cleaned:
        cleaned = "https://" + cleaned
    try:
        parts = urlsplit(cleaned)
        host = (parts.hostname or "").casefold().rstrip(".")
        if (
            parts.scheme not in {"http", "https"}
            or parts.username is not None
            or parts.password is not None
            or parts.port is not None
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise InvalidPrioritySite("Yalnız saytın əsas domenini göstərin.")
    except ValueError as error:
        raise InvalidPrioritySite("Domen formatı yanlışdır.") from error
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 2
        or any(not _LABEL.fullmatch(label) for label in labels)
        or len(labels[-1]) < 2
        or host.endswith((".local", ".internal", ".test", ".invalid", ".localhost"))
    ):
        raise InvalidPrioritySite("Etibarlı ictimai sayt domeni yazın.")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host
    raise InvalidPrioritySite("IP ünvanları prioritet sayt ola bilməz.")


class PrioritySitesService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list_domains(self) -> tuple[str, ...]:
        async with self.session_factory() as session:
            stored = await session.scalar(
                select(SystemMetadata.value).where(SystemMetadata.key == PRIORITY_SITES_KEY)
            )
        if not stored:
            return ()
        try:
            data = json.loads(stored)
            if not isinstance(data, list):
                return ()
            entries = [
                normalize_priority_domain(item)
                for item in cast(list[object], data)
                if isinstance(item, str)
            ]
            return tuple(dict.fromkeys(entries))[:MAX_PRIORITY_SITES]
        except (ValueError, TypeError):
            return ()

    async def update(self, actor_telegram_id: int, action: str, raw_domain: str) -> tuple[str, ...]:
        """Atomic admin-only add/remove, with an audit trail and row-level lock."""

        if action not in {"add", "remove"}:
            raise ValueError("Unsupported site action")
        domain = normalize_priority_domain(raw_domain)
        async with self.session_factory.begin() as session:
            actor = await session.scalar(
                select(User).where(
                    User.telegram_user_id == actor_telegram_id,
                    User.status == UserStatus.ACTIVE,
                    User.role == UserRole.ADMIN,
                )
            )
            if actor is None:
                raise PrioritySitePermissionDenied(
                    "Yalnız administrator sayt siyahısını dəyişə bilər."
                )
            await session.execute(
                insert(SystemMetadata)
                .values(key=PRIORITY_SITES_KEY, value="[]")
                .on_conflict_do_nothing(index_elements=[SystemMetadata.key])
            )
            row = await session.scalar(
                select(SystemMetadata)
                .where(SystemMetadata.key == PRIORITY_SITES_KEY)
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("Priority website metadata unavailable")
            try:
                loaded = json.loads(row.value or "[]")
                domains = list(
                    dict.fromkeys(
                        normalize_priority_domain(item) for item in loaded if isinstance(item, str)
                    )
                )
            except (ValueError, TypeError):
                domains = []
            if action == "add":
                if domain in domains:
                    return tuple(domains)
                if len(domains) >= MAX_PRIORITY_SITES:
                    raise InvalidPrioritySite(f"Ən çox {MAX_PRIORITY_SITES} prioritet sayt olar.")
                domains.append(domain)
            else:
                if domain not in domains:
                    return tuple(domains)
                domains.remove(domain)
            row.value = json.dumps(domains, ensure_ascii=False)
            session.add(
                AuditEvent(
                    actor_user_id=actor.id,
                    action=(
                        "WEB_PRIORITY_SITE_ADDED"
                        if action == "add"
                        else "WEB_PRIORITY_SITE_REMOVED"
                    ),
                    entity_type="WEB_PRIORITY_DOMAIN",
                    entity_id=domain,
                    event_metadata={"domain": domain},
                )
            )
            return tuple(domains)
