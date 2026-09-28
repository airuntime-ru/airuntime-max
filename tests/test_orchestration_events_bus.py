"""Tests for services/orchestration/events_bus.py: persist-then-fan-out ordering in emit(),
plain EventBus pub/sub semantics, and stream_events()'s replay-then-tail behavior - including
the subscribe-before-replay ordering that prevents an event published during the replay window
from being lost (see stream_events()'s own docstring)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.orm import Session
from tests.conftest import TestingSessionLocal

from src.db.models.chat import Chat
from src.db.models.project import Project
from src.db.models.user import User
from src.services.orchestration import events_bus
from src.services.orchestration import repository as repository_module
from src.services.orchestration.repository import (
    AgentTaskRepository,
    OrchestrationPlanRepository,
    OrchestrationRunRepository,
    RunEventRepository,
)


def _make_user(db: Session) -> User:
    user = User(email=f"{uuid.uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    return user


def _make_project(db: Session, user: User) -> Project:
    project = Project(user_id=user.id, type="website", name="Test project")
    db.add(project)
    db.flush()
    return project


def _make_chat(db: Session, project: Project) -> Chat:
    chat = Chat(project_id=project.id)
    db.add(chat)
    db.flush()
    return chat


@pytest.fixture()
def run(db: Session):
    user = _make_user(db)
    project = _make_project(db, user)
    chat = _make_chat(db, project)
    return OrchestrationRunRepository(db).create(
        project_id=project.id, chat_id=chat.id, user_id=user.id
    )


@pytest.fixture()
def db_factory(db: Session):
    """Fresh Session objects bound to the SAME connection/transaction as the `db` fixture, so
    they see everything `db` has flushed (but not committed, since the outer test transaction
    always rolls back) - stream_events() opens its own short-lived session via exactly this
    kind of factory in production (a fresh SessionLocal()), so this mirrors that shape while
    staying inside the test's single rolled-back transaction."""

    def factory() -> Session:
        return TestingSessionLocal(bind=db.get_bind())

    return factory


@pytest.fixture(autouse=True)
def _reset_event_bus():
    # The module-level `_bus` singleton is process-wide - clear its subscriber map around each
    # test so a run_id collision or a leaked subscription from a failed test can't bleed in.
    events_bus._bus._subscribers.clear()
    yield
    events_bus._bus._subscribers.clear()


async def _collect(aiter, count: int, *, timeout: float = 2.0) -> list[dict]:
    items = []
    for _ in range(count):
        items.append(await asyncio.wait_for(aiter.__anext__(), timeout=timeout))
    return items


class TestEventBusPubSub:
    def test_publish_with_no_subscribers_is_a_noop(self) -> None:
        bus = events_bus.EventBus()
        bus.publish("run-1", {"seq": 1})  # must not raise

    def test_subscriber_receives_published_envelope(self) -> None:
        bus = events_bus.EventBus()
        sub = bus.subscribe("run-1")
        bus.publish("run-1", {"seq": 1, "event_type": "run_created"})
        assert sub.queue.get_nowait() == {"seq": 1, "event_type": "run_created"}

    def test_publish_only_reaches_subscribers_of_that_run_id(self) -> None:
        bus = events_bus.EventBus()
        sub_a = bus.subscribe("run-a")
        sub_b = bus.subscribe("run-b")
        bus.publish("run-a", {"seq": 1})
        assert sub_a.queue.get_nowait() == {"seq": 1}
        assert sub_b.queue.empty()

    def test_multiple_subscribers_to_the_same_run_all_receive_it(self) -> None:
        bus = events_bus.EventBus()
        sub1 = bus.subscribe("run-1")
        sub2 = bus.subscribe("run-1")
        bus.publish("run-1", {"seq": 1})
        assert sub1.queue.get_nowait() == {"seq": 1}
        assert sub2.queue.get_nowait() == {"seq": 1}

    def test_unsubscribe_stops_further_delivery(self) -> None:
        bus = events_bus.EventBus()
        sub = bus.subscribe("run-1")
        bus.unsubscribe("run-1", sub)
        bus.publish("run-1", {"seq": 1})
        assert sub.queue.empty()

    def test_unsubscribe_last_subscriber_cleans_up_the_run_entry(self) -> None:
        bus = events_bus.EventBus()
        sub = bus.subscribe("run-1")
        bus.unsubscribe("run-1", sub)
        assert "run-1" not in bus._subscribers

    def test_unsubscribe_unknown_run_id_is_a_noop(self) -> None:
        bus = events_bus.EventBus()
        sub = bus.subscribe("run-1")
        bus.unsubscribe("does-not-exist", sub)  # must not raise


class TestGetEventBus:
    def test_returns_the_same_singleton_every_call(self) -> None:
        assert events_bus.get_event_bus() is events_bus.get_event_bus()


class TestEmit:
    def test_persists_row_and_returns_matching_envelope(self, db: Session, run) -> None:
        envelope = events_bus.emit(
            db, run_id=run.id, event_type="run_created", payload={"foo": "bar"}
        )
        assert envelope["event_type"] == "run_created"
        assert envelope["payload"] == {"foo": "bar"}
        assert envelope["seq"] == 1

        rows = RunEventRepository(db).list_since(run.id, after_seq=0)
        assert len(rows) == 1
        assert rows[0].event_type == "run_created"

    def test_seq_increments_per_run(self, db: Session, run) -> None:
        first = events_bus.emit(db, run_id=run.id, event_type="run_created", payload={})
        second = events_bus.emit(db, run_id=run.id, event_type="planning_started", payload={})
        assert first["seq"] == 1
        assert second["seq"] == 2

    def test_publishes_to_a_live_subscriber_after_persisting(self, db: Session, run) -> None:
        sub = events_bus.get_event_bus().subscribe(str(run.id))
        events_bus.emit(db, run_id=run.id, event_type="run_created", payload={"a": 1})
        received = sub.queue.get_nowait()
        assert received["event_type"] == "run_created"
        assert received["payload"] == {"a": 1}

    def test_unrecognized_event_type_warns_but_still_persists_and_publishes(
        self, db: Session, run, caplog: pytest.LogCaptureFixture
    ) -> None:
        sub = events_bus.get_event_bus().subscribe(str(run.id))
        with caplog.at_level("WARNING"):
            envelope = events_bus.emit(db, run_id=run.id, event_type="not_a_real_type", payload={})
        assert "not_a_real_type" in caplog.text
        assert envelope["event_type"] == "not_a_real_type"
        assert sub.queue.get_nowait()["event_type"] == "not_a_real_type"

    def test_task_id_is_stringified_in_the_envelope(self, db: Session, run) -> None:
        plan = OrchestrationPlanRepository(db).create_version(
            run_id=run.id, version=1, graph_json="{}"
        )
        task = AgentTaskRepository(db).create(
            run_id=run.id,
            plan_id=plan.id,
            local_id="a",
            title="A",
            role="implementer",
            execution_kind="specialist_agent",
            status="pending",
            depends_on_json="[]",
        )
        envelope = events_bus.emit(
            db, run_id=run.id, event_type="task_started", payload={}, task_id=task.id
        )
        assert envelope["task_id"] == str(task.id)

    def test_no_task_id_is_none_in_the_envelope(self, db: Session, run) -> None:
        envelope = events_bus.emit(db, run_id=run.id, event_type="run_created", payload={})
        assert envelope["task_id"] is None


class TestStreamEventsReplay:
    @pytest.mark.asyncio
    async def test_replays_existing_rows_in_seq_order(self, db: Session, run, db_factory) -> None:
        events_bus.emit(db, run_id=run.id, event_type="run_created", payload={"n": 1})
        events_bus.emit(db, run_id=run.id, event_type="planning_started", payload={"n": 2})
        events_bus.emit(db, run_id=run.id, event_type="run_completed", payload={"n": 3})

        received = [e async for e in events_bus.stream_events(db_factory, str(run.id), after_seq=0)]
        assert [e["event_type"] for e in received] == [
            "run_created",
            "planning_started",
            "run_completed",
        ]
        assert [e["payload"]["n"] for e in received] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_stops_at_a_terminal_event_during_replay_without_reaching_live_tail(
        self, db: Session, run, db_factory
    ) -> None:
        events_bus.emit(db, run_id=run.id, event_type="run_failed", payload={})
        events_bus.emit(db, run_id=run.id, event_type="run_created", payload={})  # must be ignored

        received = [e async for e in events_bus.stream_events(db_factory, str(run.id), after_seq=0)]
        assert [e["event_type"] for e in received] == ["run_failed"]

    @pytest.mark.asyncio
    async def test_after_seq_excludes_already_seen_rows(self, db: Session, run, db_factory) -> None:
        events_bus.emit(db, run_id=run.id, event_type="run_created", payload={})
        second = events_bus.emit(db, run_id=run.id, event_type="planning_started", payload={})
        events_bus.emit(db, run_id=run.id, event_type="run_completed", payload={})

        received = [
            e
            async for e in events_bus.stream_events(
                db_factory, str(run.id), after_seq=second["seq"] - 1
            )
        ]
        assert [e["seq"] for e in received] == [second["seq"], second["seq"] + 1]


class TestStreamEventsLiveTail:
    @pytest.mark.asyncio
    async def test_tails_events_emitted_after_replay_finishes(
        self, db: Session, run, db_factory
    ) -> None:
        gen = events_bus.stream_events(db_factory, str(run.id), after_seq=0)
        first = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)  # let stream_events reach its subscribe() + queue.get()
        events_bus.emit(db, run_id=run.id, event_type="run_created", payload={"live": True})

        envelope = await asyncio.wait_for(first, timeout=2.0)
        assert envelope["event_type"] == "run_created"
        assert envelope["payload"] == {"live": True}

    @pytest.mark.asyncio
    async def test_terminal_event_on_live_tail_ends_the_generator(
        self, db: Session, run, db_factory
    ) -> None:
        gen = events_bus.stream_events(db_factory, str(run.id), after_seq=0)
        task = asyncio.ensure_future(_collect(gen, 1))
        await asyncio.sleep(0.05)
        events_bus.emit(db, run_id=run.id, event_type="run_cancelled", payload={})
        await asyncio.wait_for(task, timeout=2.0)

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_unsubscribes_when_the_consumer_stops_early(
        self, db: Session, run, db_factory
    ) -> None:
        gen = events_bus.stream_events(db_factory, str(run.id), after_seq=0)
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)
        events_bus.emit(db, run_id=run.id, event_type="run_created", payload={})
        await asyncio.wait_for(task, timeout=2.0)

        assert str(run.id) in events_bus._bus._subscribers
        await gen.aclose()
        assert str(run.id) not in events_bus._bus._subscribers


class TestStreamEventsReplayRace:
    @pytest.mark.asyncio
    async def test_event_published_during_the_replay_query_is_not_lost(
        self, db: Session, run, db_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test for the exact race stream_events()'s docstring calls out: it must
        subscribe to the live bus BEFORE running the DB replay query, so anything emitted in the
        window between "replay snapshot taken" and "live tail begins" is still delivered (via the
        live queue, deduped against `last_seq`) instead of silently disappearing. Simulated here
        by making the replay query itself emit a new event as a side effect, standing in for a
        concurrent engine task doing the same thing at the same real wall-clock moment."""
        events_bus.emit(db, run_id=run.id, event_type="run_created", payload={})

        original_list_since = repository_module.RunEventRepository.list_since
        emitted_concurrently = {"done": False}

        def _list_since_with_concurrent_emit(self, run_id, *, after_seq=0, limit=1000):
            rows = original_list_since(self, run_id, after_seq=after_seq, limit=limit)
            if not emitted_concurrently["done"]:
                emitted_concurrently["done"] = True
                events_bus.emit(db, run_id=run.id, event_type="run_completed", payload={})
            return rows

        monkeypatch.setattr(
            repository_module.RunEventRepository, "list_since", _list_since_with_concurrent_emit
        )

        received = [e async for e in events_bus.stream_events(db_factory, str(run.id), after_seq=0)]
        assert [e["event_type"] for e in received] == ["run_created", "run_completed"]
