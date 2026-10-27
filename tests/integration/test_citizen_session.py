"""Vətəndaş sessiyası və limitlər Redis-də."""
import json

import pytest

from app.citizen.session import CitizenSession, RedisLimiter, RedisSessionStore

pytestmark = pytest.mark.integration


def test_session_round_trip_with_ttl(redis_client):
    store = RedisSessionStore(redis_client, ttl_seconds=24 * 3600)
    assert store.load(7) == CitizenSession()  # yoxdursa boş sessiya
    store.save(7, CitizenSession(step="fin", draft={"subject": "Pensiya", "summary": "s", "description": "d"}))
    loaded = store.load(7)
    assert loaded.step == "fin" and loaded.draft["subject"] == "Pensiya"
    assert 0 < redis_client.ttl("citizen:session:7") <= 24 * 3600


def test_unknown_fields_from_an_older_version_are_ignored(redis_client):
    redis_client.set("citizen:session:8", json.dumps({"step": "name", "removed_field": 1}))
    assert RedisSessionStore(redis_client, ttl_seconds=3600).load(8).step == "name"


def test_reset_clears_everything():
    session = CitizenSession(step="confirm", draft={"subject": "x"}, redo=True)
    session.reset()
    assert session == CitizenSession()


def test_limiter_counts_per_user_and_deduplicates_updates(redis_client):
    limiter = RedisLimiter(redis_client)
    assert [limiter.hit(1), limiter.hit(1), limiter.hit(2)] == [1, 2, 1]
    assert limiter.first_time(555) and not limiter.first_time(555)
