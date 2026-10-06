"""Telegram-safe response formatting and semantic splitting."""


def split_telegram_text(text: str, *, limit: int = 4096) -> list[str]:
    if len(text) <= limit:
        return [text]
    remaining = text.strip()
    parts: list[str] = []
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
        if cut < limit // 2:
            cut = limit
        part = remaining[:cut].rstrip()
        parts.append(part)
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts
