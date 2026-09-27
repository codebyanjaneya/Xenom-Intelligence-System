from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models import Entity
from app.schemas.entity import EntityRegistryOut

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=list[EntityRegistryOut], dependencies=[Depends(get_current_user)])
async def list_entities(session: AsyncSession = Depends(get_session)) -> list[EntityRegistryOut]:
    rows = await session.scalars(select(Entity).order_by(Entity.canonical_name.asc()))
    return [
        EntityRegistryOut(
            id=str(entity.id),
            canonical_name=entity.canonical_name,
            canonical_key=entity.canonical_key,
            entity_type=entity.entity_type,
            status=entity.status,
        )
        for entity in rows
    ]