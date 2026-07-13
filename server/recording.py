"""Mechanical persistence concerns for the session recorder.

`UsageTotals` accumulates per-session token/character totals. `RecordDrainQueue`
owns the in-memory queue, the background drain loop, and the batched flush
dispatch to the storage repository — keeping that machinery out of
`SessionRecorder`, which orchestrates the session lifecycle.
"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass

from loguru import logger

from storage import insert_events, insert_messages, insert_metrics

BATCH_MAX = 50
BATCH_INTERVAL_S = 1.0
QUEUE_MAX = 10_000


class RecordKind(enum.Enum):
    """Which repository method a queued row is destined for."""

    MESSAGE = enum.auto()
    EVENT = enum.auto()
    METRIC = enum.auto()


@dataclass
class UsageTotals:
    """Running token / character totals for a session, finalized onto its row."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    tts_chars: int = 0

    def add_llm(
        self,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cache_read_input_tokens: int | None = None,
        cache_creation_input_tokens: int | None = None,
    ) -> None:
        self.prompt_tokens += prompt_tokens or 0
        self.completion_tokens += completion_tokens or 0
        self.cache_read_tokens += cache_read_input_tokens or 0
        self.cache_creation_tokens += cache_creation_input_tokens or 0

    def add_tts(self, chars: float) -> None:
        self.tts_chars += int(chars)


class RecordDrainQueue:
    """In-memory queue + background drain that batch-flushes rows to storage.

    Enqueue is non-blocking (microsecond cost, zero awaits) so the audio pipeline
    never waits on the network. A background task drains in batches up to
    BATCH_MAX every BATCH_INTERVAL_S; `stop()` makes the loop do one final flush
    and exit. Trade-off: a crash can lose up to one batch interval of rows.
    """

    def __init__(self, pool):
        self._pool = pool
        self._queue: asyncio.Queue[tuple[RecordKind, dict] | None] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._drain_task: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        self._drain_task = asyncio.create_task(self._drain_loop())

    def enqueue(self, kind: RecordKind, row: dict) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait((kind, row))
        except asyncio.QueueFull:
            logger.warning(f"Record queue full; dropping {kind.name} row")

    async def stop(self) -> None:
        """Signal the drain loop to do a final flush and exit (idempotent)."""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(None)  # sentinel
        except asyncio.QueueFull:
            pass
        if self._drain_task is not None:
            try:
                await asyncio.wait_for(self._drain_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Record drain loop did not finish in time; cancelling")
                self._drain_task.cancel()
                try:
                    await self._drain_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _drain_loop(self) -> None:
        """Pull from the queue, batch, flush to Supabase."""
        try:
            while True:
                batch: list[tuple[RecordKind, dict]] = []
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=BATCH_INTERVAL_S)
                except asyncio.TimeoutError:
                    continue
                if item is None:  # sentinel: shutdown
                    break
                batch.append(item)
                # Drain whatever else is sitting in the queue, up to BATCH_MAX.
                while len(batch) < BATCH_MAX:
                    try:
                        nxt = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if nxt is None:
                        await self._flush(batch)
                        return
                    batch.append(nxt)
                await self._flush(batch)
        except asyncio.CancelledError:
            # Best-effort final flush.
            leftover: list[tuple[RecordKind, dict]] = []
            while True:
                try:
                    leftover.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._flush([x for x in leftover if x is not None])
            raise

    async def _flush(self, batch: list[tuple[RecordKind, dict]]) -> None:
        if not batch:
            return
        # The repository method for each kind owns its table name + column order.
        inserters = {
            RecordKind.MESSAGE: insert_messages,
            RecordKind.EVENT: insert_events,
            RecordKind.METRIC: insert_metrics,
        }
        by_kind: dict[RecordKind, list[dict]] = {}
        for kind, row in batch:
            by_kind.setdefault(kind, []).append(row)
        for kind, rows in by_kind.items():
            try:
                await inserters[kind](self._pool, rows)
            except Exception as e:
                logger.exception(f"Record flush of {kind.name} rows failed: {e}")
