"""Per-user binary feature grants (owner contract V2 §2).

The owner asked for a simple ACTIVE/INACTIVE toggle per feature, not a per-feature
CRUD matrix. This module is the single place that decides whether an authenticated
actor may use a capability. Hiding a Telegram button is presentation only; every
handler, callback and service entry point asks this policy.

An administrator is never locked out by a toggle: role ADMIN bypasses grants, and
the administration service refuses to edit an administrator's grid at all.
"""

from collections.abc import Mapping
from enum import StrEnum

from office_assistant.infrastructure.database.models import User, UserRole, UserStatus


class FeatureKey(StrEnum):
    """Stable internal keys. Localized button labels are never authorization keys."""

    AI_CHAT = "ai_chat"
    DOCUMENT_SEARCH = "document_search"
    DOCUMENT_INGEST = "document_ingest"
    PRODUCT_SEARCH = "product_search"
    IMAGE_SEARCH = "image_search"
    ORDERS = "orders"
    SALES_REPORTS = "sales_reports"
    EXTERNAL_SEARCH = "external_search"
    INVENTORY = "inventory"
    REMINDERS = "reminders"


FEATURE_KEYS: tuple[FeatureKey, ...] = tuple(FeatureKey)

# ASSUMPTION (docs/OPEN-QUESTIONS.md Q46): approval keeps the assistant usable. Every
# capability that already worked before this migration stays enabled for existing and
# newly approved users; `orders` is a new capability and starts disabled until an admin
# enables it. A missing row therefore means the documented baseline, never "denied".
BASELINE_ENABLED: frozenset[FeatureKey] = frozenset(
    key for key in FEATURE_KEYS if key is not FeatureKey.ORDERS
)


def parse_feature(value: object) -> FeatureKey | None:
    """Accept only a known key; client-supplied callback text is untrusted."""

    if isinstance(value, FeatureKey):
        return value
    if not isinstance(value, str):
        return None
    try:
        return FeatureKey(value)
    except ValueError:
        return None


def baseline_grants() -> dict[str, bool]:
    """The explicit grid written when a user is approved (and by migration 0025)."""

    return {key.value: key in BASELINE_ENABLED for key in FEATURE_KEYS}


def can_use_feature(
    user: User | None, grants: Mapping[str, bool], feature: FeatureKey | str
) -> bool:
    """Fail closed: no active account, unknown key or an explicit disable all deny."""

    key = parse_feature(feature)
    if key is None or user is None or user.status != UserStatus.ACTIVE:
        return False
    if user.role == UserRole.ADMIN:
        return True
    stored = grants.get(key.value)
    if stored is None:
        return key in BASELINE_ENABLED
    return bool(stored)


def allowed_features(user: User | None, grants: Mapping[str, bool]) -> tuple[FeatureKey, ...]:
    """Menu generation uses the same decision as backend authorization."""

    return tuple(key for key in FEATURE_KEYS if can_use_feature(user, grants, key))
