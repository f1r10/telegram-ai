"""OpenAI-schema adapter without coupling Phase 9 tools to a provider."""

from office_assistant.business_tools.registry import BusinessToolRegistry


class OpenAIToolSchemaAdapter:
    def __init__(self, registry: BusinessToolRegistry) -> None:
        self.registry = registry

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "name": item["name"],
                "description": item["description"],
                "parameters": item["input_schema"],
                "strict": True,
            }
            for item in self.registry.schemas()
        ]
