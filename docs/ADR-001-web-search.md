# ADR-001: V1 üçün xərc-optimallaşdırılmış web search

Status: Təsdiqlənib
Faz: Phase 6

## Provider abstraction

Application logic, agent routing, `/internet` və `/tamaxtar` yalnız `WebSearchProvider` interfeysinə bağlı olacaq. SearXNG, Tavily, Exa və ya başqa vendor birbaşa biznes məntiqinə qoşulmayacaq.

## Primary provider: self-hosted SearXNG

SearXNG eyni Docker Compose stack-də, application VM-də işləyən əsas provider olacaq. İctimaiyyətə açılmayacaq.

## Fallback provider-lər

1. SearXNGProvider
2. TavilyProvider
   (Eyni sorğu bütün provider-lərə paralel göndərilməyəcək).

## Xərc və cache nəzarəti

PostgreSQL-də `web_search_cache` saxlanılacaq. Təzəlik tələb edən sorğular köhnə cache-dən qaytarılmayacaq. Redis əlavə edilməyəcək.

## Daxili və xarici məlumatın ayrılması

Web nəticələri external evidence olaraq qalacaq. İstənilən yaddaşa yazılma əməliyyatı yalnız açıq `/saxla` komandası ilə olmalıdır.

## Resilience və konfiqurasiya

Bounded connection/request timeout olacaq. Xəta halında Telegram bot çökməyəcək, Azərbaycan dilində xəta verəcək.
