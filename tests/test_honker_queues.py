"""Work queue tests using a file-backed SQLite DB (honker requires WAL)."""

import pytest
import pytest_asyncio
import honker


@pytest_asyncio.fixture()
async def hdb(tmp_path):
    db = honker.open(str(tmp_path / "test.db"))
    # Bootstrap schema
    db.queue("snapshot", max_attempts=5, visibility_timeout_s=30)
    db.queue("hot_reload", max_attempts=3, visibility_timeout_s=20)
    yield db


@pytest.mark.asyncio
async def test_snapshot_queue_enqueue_and_ack(hdb):
    q = hdb.queue("snapshot", max_attempts=5, visibility_timeout_s=30)
    q.enqueue({"action": "flush_snapshot"})

    job = q.claim_one("w1")
    assert job is not None
    assert job.payload == {"action": "flush_snapshot"}
    assert job.attempts == 1

    job.ack()
    assert q.claim_one("w1") is None


@pytest.mark.asyncio
async def test_snapshot_queue_retry_increments_attempts(hdb):
    q = hdb.queue("snapshot", max_attempts=5, visibility_timeout_s=30)
    q.enqueue({"action": "flush_snapshot"})

    job = q.claim_one("w1")
    assert job is not None
    job.retry(delay_s=0, error="db error")

    job2 = q.claim_one("w1")
    assert job2 is not None
    assert job2.attempts == 2
    job2.ack()


@pytest.mark.asyncio
async def test_snapshot_queue_goes_dead_after_max_attempts(hdb):
    # Use a fresh queue name so max_attempts=2 isn't overridden by the fixture
    q = hdb.queue("snapshot_dead_test", max_attempts=2, visibility_timeout_s=30)
    q.enqueue({"action": "flush_snapshot"})

    for _ in range(2):
        job = q.claim_one("w1")
        if job:
            job.retry(delay_s=0, error="fail")

    # Exhausted — no more claimable jobs
    assert q.claim_one("w1") is None


@pytest.mark.asyncio
async def test_reload_queue_enqueue_and_ack(hdb):
    q = hdb.queue("hot_reload", max_attempts=3, visibility_timeout_s=20)
    q.enqueue({"agent_id": "g_a", "grpc_port": 50051, "auth_token": "tok"})

    job = q.claim_one("w1")
    assert job is not None
    assert job.payload["agent_id"] == "g_a"
    assert job.payload["grpc_port"] == 50051
    job.ack()


@pytest.mark.asyncio
async def test_reload_queue_retry_with_backoff(hdb):
    q = hdb.queue("hot_reload", max_attempts=3, visibility_timeout_s=20)
    q.enqueue({"agent_id": "g_b", "grpc_port": 50052, "auth_token": "tok"})

    job = q.claim_one("w1")
    assert job is not None
    job.retry(delay_s=0, error="gRPC unavailable")

    job2 = q.claim_one("w1")
    assert job2 is not None
    assert job2.attempts == 2
    job2.ack()


@pytest.mark.asyncio
async def test_multiple_jobs_processed_in_order(hdb):
    q = hdb.queue("snapshot", max_attempts=5, visibility_timeout_s=30)
    for i in range(3):
        q.enqueue({"seq": i})

    results = []
    for _ in range(3):
        job = q.claim_one("w1")
        assert job is not None
        results.append(job.payload["seq"])
        job.ack()

    assert results == [0, 1, 2]
