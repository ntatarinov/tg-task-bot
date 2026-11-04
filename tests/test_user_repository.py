from __future__ import annotations

import pytest

from app.db.repositories import UserRepository


@pytest.mark.asyncio
async def test_get_by_username_finds_exact_match(session):
    repo = UserRepository(session)
    await repo.get_or_create(telegram_user_id=1, username="Alice", display_name="Alice A")
    await session.commit()

    found = await repo.get_by_username("Alice")
    assert found is not None
    assert found.telegram_user_id == 1


@pytest.mark.asyncio
async def test_get_by_username_ignores_leading_at_and_case(session):
    repo = UserRepository(session)
    await repo.get_or_create(telegram_user_id=2, username="Bob", display_name="Bob B")
    await session.commit()

    assert (await repo.get_by_username("@bob")) is not None
    assert (await repo.get_by_username("BOB")) is not None
    assert (await repo.get_by_username("@BoB")) is not None


@pytest.mark.asyncio
async def test_get_by_username_returns_none_when_not_found(session):
    repo = UserRepository(session)
    assert await repo.get_by_username("nobody") is None
