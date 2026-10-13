from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.whatsapp.providers.base import WaProvider


def get_provider(db: Session, settings: Settings | None = None) -> WaProvider:
    s = settings or get_settings()
    if s.whatsapp_provider == "meta":
        from app.whatsapp.providers.meta import MetaWaProvider

        return MetaWaProvider(s)
    from app.whatsapp.providers.mock import MockWaProvider

    return MockWaProvider(db, s)
