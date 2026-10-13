from app.research.providers.base import (
    PersonRecord,
    ProfileRecord,
    ProviderError,
    ResearchProvider,
    SourceDocument,
    get_provider,
    register_provider,
)
from app.research.providers.mock import MockProvider

register_provider("mock", MockProvider)

__all__ = [
    "PersonRecord",
    "ProfileRecord",
    "ProviderError",
    "ResearchProvider",
    "SourceDocument",
    "get_provider",
    "register_provider",
]
