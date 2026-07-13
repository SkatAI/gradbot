"""The MsgOut -> Supabase mapping.

These assertions are load-bearing beyond this repo: sceance's dashboard derives
`response_latency` from the gap between `user_stopped_speaking` and
`bot_started_speaking`, and groups TTFB by `metrics.processor`. If a rename here
goes unnoticed, a panel over there quietly goes blank rather than erroring. So
the tests pin the exact strings, not just "an event was written".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import FakeRecorder
from tracing import LLM_PROCESSOR, TTS_PROCESSOR, SessionTracer, _normalize


# Real gradbot messages, as observed off the wire. The timing fields are the
# point: `time_s` really is always 0.0 on events, and `start_s`/`stop_s` really
# are an audio-timeline clock (position within the synthesized speech). Both are
# baked into these fakes so a tracer that trusts either one fails here.

def stt(text):
    return SimpleNamespace(msg_type="stt_text", text=text, time_s=None, start_s=0.0)


def tts(text, turn_idx=0, start_s=0.0):
    return SimpleNamespace(
        msg_type="tts_text", text=text, turn_idx=turn_idx, time_s=None, start_s=start_s
    )


def event(event_type):
    return SimpleNamespace(
        msg_type="event",
        event=SimpleNamespace(event_type=event_type, data=None),
        time_s=0.0,  # gradbot never populates this — see SessionTracer._ts
        start_s=None,
        text=None,
    )


class FakeClock:
    """A monotonic clock the test advances by hand, in seconds."""

    def __init__(self):
        self.now_ns = 1_000_000_000

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, seconds: float) -> None:
        self.now_ns += int(seconds * 1_000_000_000)


def tracer(recorder=None, clock=None):
    return SessionTracer(recorder or FakeRecorder(), lang="en", clock=clock or FakeClock())


# ---- event-name normalization -------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("FirstTtsAudio", "first_tts_audio"),
        ("first_tts_audio", "first_tts_audio"),
        ("EndOfTurn", "end_of_turn"),
        ("PushToLlm", "push_to_llm"),
        ("  Flushing ", "flushing"),
    ],
)
def test_normalize_accepts_either_casing(raw, expected):
    # The Rust enum is CamelCase; the wire protocol shows snake_case. We are not
    # betting the whole trace on which one the binding hands us.
    assert _normalize(raw) == expected


# ---- transcripts ---------------------------------------------------------

def test_user_speech_becomes_one_message_not_one_per_word():
    rec = FakeRecorder()
    t = tracer(rec)
    for word in ("what", "is", "the", "price"):
        t.on_msg(stt(word))
    t.on_msg(event("flushing"))

    assert rec.messages == [{"role": "user", "text": "what is the price", "language": "en"}]


def test_agent_speech_becomes_one_message_and_a_tts_char_count():
    rec = FakeRecorder()
    t = tracer(rec)
    t.on_msg(tts("Eggs", turn_idx=1))
    t.on_msg(tts("are", turn_idx=1))
    t.on_msg(tts("dear.", turn_idx=1))
    t.on_msg(event("end_tts_audio"))
    t.on_msg(event("end_of_turn"))

    assert rec.messages == [{"role": "assistant", "text": "Eggs are dear.", "language": "en"}]
    usage = [m for m in rec.metrics if m["kind"] == "tts_usage"]
    assert usage == [{
        "processor": TTS_PROCESSOR, "model": None, "kind": "tts_usage",
        "value_num": float(len("Eggs are dear.")),
    }]


def test_a_trailing_caption_after_end_tts_audio_stays_in_the_same_message():
    # Observed on a real call: tts_text captions lag the audio they describe, so
    # the last word can land *after* end_tts_audio. Closing the transcript on
    # that event split one sentence across two rows:
    #     "Sorry, there's some noise on my end - what did you"
    #     "say?"
    rec = FakeRecorder()
    t = tracer(rec)
    t.on_msg(tts("what", turn_idx=1))
    t.on_msg(tts("did", turn_idx=1))
    t.on_msg(tts("you", turn_idx=1))
    t.on_msg(event("end_tts_audio"))   # audio done...
    t.on_msg(tts("say?", turn_idx=1))  # ...but the caption is still coming
    t.on_msg(event("end_of_turn"))

    assert [m["text"] for m in rec.messages] == ["what did you say?"]


def test_a_new_turn_index_closes_the_previous_agent_turn():
    # Barge-in can start turn N+1 without an end event for turn N. Without this
    # the two turns would be concatenated into one nonsense message.
    rec = FakeRecorder()
    t = tracer(rec)
    t.on_msg(tts("first", turn_idx=1))
    t.on_msg(tts("second", turn_idx=2))
    t.on_msg(event("end_of_turn"))

    assert [m["text"] for m in rec.messages] == ["first", "second"]


def test_close_flushes_a_half_finished_turn():
    # Hanging up mid-sentence does not un-say the sentence.
    rec = FakeRecorder()
    t = tracer(rec)
    t.on_msg(stt("hello"))
    t.on_msg(tts("Hi there", turn_idx=1))
    t.close()

    assert {m["role"] for m in rec.messages} == {"user", "assistant"}


# ---- the event vocabulary the dashboard reads ----------------------------

def test_full_turn_emits_the_kinds_the_dashboard_expects():
    rec = FakeRecorder()
    t = tracer(rec)
    for kind in ("flushing", "push_to_llm", "first_word", "first_tts_audio", "end_tts_audio"):
        t.on_msg(event(kind))

    assert rec.event_kinds() == [
        "user_stopped_speaking",  # <- response_latency starts here
        "llm_start",
        "bot_started_speaking",   # <- and ends here
        "tts_start",
        "bot_stopped_speaking",
        "tts_stop",
        "llm_end",
    ]


# ---- the clock -----------------------------------------------------------
#
# This is the section that matters most. gradbot's own timing fields look
# authoritative and are not: `time_s` is hardcoded 0.0 on events, and
# `start_s`/`stop_s` are a position *within the synthesized audio*. A tracer that
# believes either one silently records every latency as zero — the transcript
# still looks perfect, so nothing else catches it.

def test_timestamps_come_from_the_wall_clock_not_gradbots_zeroed_time_s():
    rec = FakeRecorder()
    clock = FakeClock()
    t = tracer(rec, clock)

    t.on_msg(event("flushing"))
    clock.advance(0.55)
    t.on_msg(event("first_tts_audio"))

    # Events from the *same* gradbot message share a timestamp on purpose
    # (bot_started_speaking and tts_start are the same instant). Events from
    # different messages must not.
    by_kind = {e["kind"]: e["timestamp_ns"] for e in rec.events}
    assert by_kind["bot_started_speaking"] > by_kind["user_stopped_speaking"], (
        "both events landed on the same timestamp — the tracer is trusting "
        "MsgOut.time_s, which gradbot always reports as 0.0"
    )


def test_response_latency_is_recoverable_from_the_events():
    rec = FakeRecorder()
    clock = FakeClock()
    t = tracer(rec, clock)

    t.on_msg(event("flushing"))
    clock.advance(0.55)
    t.on_msg(event("first_tts_audio"))

    by_kind = {e["kind"]: e["timestamp_ns"] for e in rec.events}
    latency_s = (by_kind["bot_started_speaking"] - by_kind["user_stopped_speaking"]) / 1e9
    assert latency_s == pytest.approx(0.55, abs=1e-6)


def test_audio_timeline_fields_are_never_mistaken_for_a_session_clock():
    # tts_text carries start_s, and it restarts near 0 on every turn. If the
    # tracer reached for it, turn 2 would appear to happen before turn 1.
    rec = FakeRecorder()
    clock = FakeClock()
    t = tracer(rec, clock)

    t.on_msg(tts("one", turn_idx=1, start_s=0.24))
    t.on_msg(event("end_tts_audio"))
    clock.advance(30.0)
    t.on_msg(tts("two", turn_idx=2, start_s=0.24))  # same audio offset, 30s later
    t.on_msg(event("end_tts_audio"))

    turn_ends = [e["timestamp_ns"] for e in rec.events if e["kind"] == "bot_stopped_speaking"]
    assert len(turn_ends) == 2
    assert turn_ends[1] - turn_ends[0] == pytest.approx(30e9, rel=1e-6)


# ---- per-stage TTFB ------------------------------------------------------

def test_stage_ttfb_is_derived_from_the_event_gaps():
    rec = FakeRecorder()
    clock = FakeClock()
    t = tracer(rec, clock)

    t.on_msg(event("push_to_llm"))
    clock.advance(0.3)
    t.on_msg(event("first_word"))       # LLM took 300ms to first token
    clock.advance(0.2)
    t.on_msg(event("first_tts_audio"))  # TTS took 200ms to first byte

    ttfb = {m["processor"]: m["value_num"] for m in rec.metrics if m["kind"] == "ttfb"}
    assert ttfb[LLM_PROCESSOR] == pytest.approx(0.3, abs=1e-6)
    assert ttfb[TTS_PROCESSOR] == pytest.approx(0.2, abs=1e-6)


def test_ttfb_is_never_zero_for_a_turn_that_actually_took_time():
    # The failure this is here to prevent: plausible-looking rows, all 0.000s.
    rec = FakeRecorder()
    clock = FakeClock()
    t = tracer(rec, clock)

    t.on_msg(event("push_to_llm"))
    clock.advance(0.78)
    t.on_msg(event("first_word"))
    clock.advance(0.40)
    t.on_msg(event("first_tts_audio"))

    ttfb = [m["value_num"] for m in rec.metrics if m["kind"] == "ttfb"]
    assert ttfb and all(v > 0 for v in ttfb)


def test_no_llm_usage_rows_are_ever_written():
    # v1 records no token counts — gradbot's Rust core never surfaces them. If
    # this ever starts failing, sessions.total_*_tokens can stop being zero and
    # latency_collector's pinned LLM_PROCESSOR can go back to being inferred.
    rec = FakeRecorder()
    t = tracer(rec)
    t.on_msg(event("push_to_llm"))
    t.on_msg(event("first_word"))
    t.on_msg(tts("hi", turn_idx=1))
    t.on_msg(event("end_tts_audio"))

    assert not [m for m in rec.metrics if m["kind"] == "llm_usage"]


# ---- robustness ----------------------------------------------------------

def test_unknown_events_are_recorded_rather_than_dropped():
    # If gradbot adds or renames an event, it should show up in the DB so we can
    # see it, not vanish silently.
    rec = FakeRecorder()
    t = tracer(rec)
    t.on_msg(event("SomeNewThing"))

    assert rec.event_kinds() == ["some_new_thing"]


def test_a_tracing_bug_never_kills_the_call():
    class Exploding(FakeRecorder):
        def record_event(self, *a, **kw):
            raise RuntimeError("boom")

    t = tracer(Exploding())
    t.on_msg(event("flushing"))  # must not raise
