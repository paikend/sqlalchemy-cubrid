"""EXISTS and AND/OR as SELECT-list items run on a live CUBRID (#966).

CUBRID rejects ``SELECT EXISTS (...)`` and ``SELECT a AND b``; the dialect
groups such a column. Sync, including the ORM ``query(...).exists()`` idiom,
and async.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import Column, Integer, MetaData, String, Table, orm
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from scripts.integration_urls import async_url

pytestmark = pytest.mark.integration

_DEFAULT_SYNC_URL = "cubrid://dba@localhost:33000/testdb"
_metadata = MetaData()
_items = Table(
    f"bool_cols_{uuid.uuid4().hex[:8]}",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(10)),
)


class _Base(orm.DeclarativeBase):
    pass


class _Item(_Base):
    __table__ = _items


def _sync_url() -> str:
    return os.environ.get("CUBRID_TEST_URL", _DEFAULT_SYNC_URL)


@pytest.fixture(scope="module")
def engine() -> Iterator[sa.Engine]:
    eng = sa.create_engine(_sync_url())
    _metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(_items.insert(), [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    try:
        yield eng
    finally:
        _metadata.drop_all(eng)
        eng.dispose()


def _columns() -> list[tuple[str, sa.ColumnElement[bool], bool]]:
    found = sa.exists().where(_items.c.name == "a")
    missing = sa.exists().where(_items.c.name == "z")
    return [
        ("exists", found, True),
        ("missing", missing, False),
        ("not-exists", ~missing, True),
        ("and", sa.and_(_items.c.name == "a", _items.c.id == 1), True),
        ("or", sa.or_(_items.c.name == "z", _items.c.id == 2), False),
    ]


@pytest.mark.parametrize(
    ("label", "column", "expected"), _columns(), ids=[c[0] for c in _columns()]
)
def test_boolean_column(engine: sa.Engine, label: str, column, expected: bool) -> None:
    stmt = sa.select(column.label(label))
    if label in ("and", "or"):
        stmt = stmt.select_from(_items).where(_items.c.id == 1)
    with engine.connect() as conn:
        assert bool(conn.execute(stmt).scalar_one()) is expected


def test_orm_query_exists(engine: sa.Engine) -> None:
    with orm.Session(engine) as session:
        assert session.query(session.query(_Item).filter(_Item.name == "a").exists()).scalar()
        assert not session.scalar(sa.select(sa.select(_Item).where(_Item.name == "z").exists()))


@pytest_asyncio.fixture
async def aengine(engine: sa.Engine) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(async_url(_sync_url(), os.environ.get("CUBRID_TEST_AURL")))
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_async_exists_column(aengine: AsyncEngine) -> None:
    async with aengine.connect() as conn:
        stmt = sa.select(sa.exists().where(_items.c.name == "a"))
        assert bool((await conn.execute(stmt)).scalar_one()) is True
