"""Research source provider abstraction.

Business logic only ever talks to ``ResearchProvider``. A provider retrieves *source material*
(official pages, news, job postings, licensed contact data). It must only use permitted access:
official APIs, licensed data feeds, or pages whose terms allow automated retrieval. Providers never
guess email addresses: an email is returned only when a cited source shows it, or a licensed
provider reports it as verified. Pattern-inferred addresses must be flagged ``email_inferred=True``
and are discarded by the pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class ProviderError(Exception):
    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class SourceDocument:
    """A piece of retrieved source material. ``doc_id`` is unique within one research run."""

    doc_id: str
    url: str
    title: str
    text: str
    source_type: str  # "official_site", "news", "job_posting", "press_release", "directory", "licensed_data"
    retrieved_at: datetime
    published_at: datetime | None = None
    official: bool = False  # published by the institution itself


@dataclass(frozen=True)
class ProfileRecord:
    official_name: str | None
    website: str | None
    city: str | None
    country: str | None
    institution_type: str | None
    campus_count: int | None
    student_size_range: str | None
    source: SourceDocument | None


@dataclass(frozen=True)
class PersonRecord:
    first_name: str
    last_name: str
    designation: str
    department: str | None
    source: SourceDocument
    excerpt: str
    email: str | None = None
    email_source: str | None = None  # "published" or "provider_verified"
    email_inferred: bool = False  # pattern guesses: always discarded
    phone: str | None = None
    linkedin_url: str | None = None
    provider_verified: bool = False


@dataclass
class ProviderResult:
    profile: ProfileRecord | None
    documents: list[SourceDocument] = field(default_factory=list)
    people: list[PersonRecord] = field(default_factory=list)
    calls: int = 0
    cost_usd: float = 0.0


class ResearchProvider(Protocol):
    name: str

    def research(self, *, name: str, website: str | None, country: str, city: str, roles: list[str]) -> ProviderResult:
        """Return profile, source documents and people for one institution. Raise ProviderError on failure."""
        ...


_REGISTRY: dict[str, Callable[[], ResearchProvider]] = {}


def register_provider(name: str, factory: Callable[[], ResearchProvider]) -> None:
    _REGISTRY[name] = factory


def get_provider(name: str) -> ResearchProvider:
    try:
        return _REGISTRY[name]()
    except KeyError as e:
        raise ValueError(f"Unknown research provider '{name}'. Registered: {', '.join(sorted(_REGISTRY))}") from e
