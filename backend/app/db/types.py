"""Column types for database objects that SQLAlchemy core does not ship.

``Vector`` mirrors the untyped pgvector ``vector`` column created by migration 0007 (``document_chunks.embedding``) so
the ORM metadata matches the real schema and ``alembic revision --autogenerate`` / ``alembic check`` no longer propose
dropping it. Vectors are still read and written with explicit SQL (``services/embeddings.py``, ``services/retrieval.py``);
the ORM never loads them (the column is mapped ``deferred``). No pgvector Python package is required.
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    """pgvector ``vector`` with no fixed dimension (the stored model decides the length)."""

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "vector"


# Let PostgreSQL reflection (used by Alembic autogenerate) recognise the type instead of reporting NullType.
ischema_names.setdefault("vector", Vector)
