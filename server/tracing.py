"""Translate gradbot's `MsgOut` stream into the shared tracing schema.

This app records calls but never reads them back — it has no dashboard. Sessions
are monitored from sceance, which reads the same database. That makes this module
a **contract with code in another repo**, and the only thing holding it up.

Sceance's session pages are built on a handful of well-known `events.kind`
strings and `metrics.processor` names: `response_latency`, for instance, is
derived purely from the gap between a `user_stopped_speaking` event and the next
`bot_started_speaking`, and TTFB is grouped by `processor`. Emit that vocabulary
from gradbot's very different event stream and every chart, table and transcript
over there keeps working.

Rename a `kind` here and a panel goes blank over there. Silently — nothing in
this repo will fail. `tests/test_tracing.py` pins the exact strings for that
reason; treat them as an API, not as local names.

    gradbot MsgOut            ->  sceance schema
    ─────────────────────────────────────────────────────────────────────
    stt_text                  ->  messages(role='user') + events(transcription)
    tts_text                  ->  messages(role='assistant') + metrics(tts_usage)
    event flushing            ->  events(user_stopped_speaking)
    event push_to_llm         ->  events(llm_start)
    event first_word          ->  metrics(ttfb, GradbotLLM)      [LLM TTFT]
    event first_tts_audio     ->  events(bot_started_speaking, tts_start)
                                  + metrics(ttfb, GradiumTTS)    [TTS TTFB]
    event end_tts_audio       ->  events(bot_stopped_speaking, tts_stop, llm_end)
    event end_of_turn         ->  flush the assistant transcript buffer
    event interrupted         ->  events(interrupted) + flush

Deliberately NOT emitted: `llm_usage` rows. Gradbot's Rust core calls the LLM
itself and never surfaces token counts, so `sessions.total_*_tokens` stay 0 for
this app, and sceance's token panels will read zero for its sessions.

Timestamps come from the local clock at message arrival, NOT from gradbot's own
timing fields — those turned out to be unusable. See `SessionTracer._ts`.

Duck-typed on purpose — nothing here imports gradbot, so the mapping is testable
outside the container (gradbot has no macOS x86_64 wheel).
"""

from __future__ import annotations

import re
import time

from loguru import logger

# Stable names for the pipeline stages. Sceance's dashboard groups TTFB rows BY
# processor, so these strings are what the operator actually reads there.
LLM_PROCESSOR = "GradbotLLM"
TTS_PROCESSOR = "GradiumTTS"

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _normalize(event_type: str) -> str:
    """`FirstTtsAudio` and `first_tts_audio` both -> `first_tts_audio`.

    The Rust enum variants are CamelCase; the bridge appears to expose them
    snake_cased (the wire protocol shows `end_of_turn`). Accept either rather
    than betting the whole trace on which one it is.
    """
    return _CAMEL_BOUNDARY.sub("_", event_type.strip()).lower()


class SessionTracer:
    """Stateful translator for one call. Not thread-safe; the output loop is serial.

    Never raises into the audio path: a tracing bug must not drop a live call, so
    `on_msg` swallows and logs. (The caller in `gradbot_session` also guards.)
    """

    def __init__(self, recorder, lang: str | None = None, *, clock=time.monotonic_ns):
        self._recorder = recorder
        self._lang = lang
        self._clock = clock

        self._user_words: list[str] = []
        self._agent_words: list[str] = []
        self._agent_turn: int | None = None

        # Per-turn stage timestamps, used to turn events into TTFB durations.
        self._t_llm_start_ns: int | None = None
        self._t_first_word_ns: int | None = None

    # ---- clock -----------------------------------------------------------

    def _ts(self, msg) -> int:
        """When this message arrived, by the local monotonic clock.

        We measured gradbot's own timing fields before trusting them, and neither
        is a session clock:

          - `MsgOut.time_s` is **always 0.0** on events. The type stub declares it,
            the Rust core sends it, but the binding never populates it. Using it
            collapsed every event in a turn onto one timestamp and made every
            latency read 0.000s.
          - `start_s` / `stop_s` (on audio and tts_text) are an **audio-timeline**
            clock — position *within the synthesized speech* (0.000, 0.080, 0.160,
            …), not time of day. Two turns an hour apart both start at 0.0.

        So arrival time in the output loop is the only honest measure available.
        The loop is serial and reads straight off the Rust channel, so the added
        delay is a sub-millisecond dequeue — small against turn latencies in the
        hundreds of ms, and constant, so it cancels out of every difference.
        """
        del msg  # kept in the signature: gradbot may yet start populating time_s
        return self._clock()

    # ---- transcript buffers ----------------------------------------------

    def _flush_user(self) -> None:
        """The user's utterance is complete — write it as one message row.

        STT arrives word-by-word, so a row per `stt_text` would shred the
        transcript into single words.
        """
        if not self._user_words:
            return
        text = " ".join(self._user_words).strip()
        self._user_words.clear()
        if text:
            # Logged, not just recorded: when a call misbehaves the first question
            # is always "did it hear me?", and the answer should be in the log
            # rather than a SQL query away.
            logger.info(f"[user]  {text}")
            self._recorder.record_message(role="user", text=text, language=self._lang)

    def _flush_agent(self) -> None:
        if not self._agent_words:
            self._agent_turn = None
            return
        text = " ".join(self._agent_words).strip()
        self._agent_words.clear()
        self._agent_turn = None
        if not text:
            return
        logger.info(f"[agent] {text}")
        self._recorder.record_message(role="assistant", text=text, language=self._lang)
        # Gradium never reports usage, so character count is the only TTS cost
        # signal there is. It feeds sessions.total_tts_chars via UsageTotals.
        self._recorder.record_metric(
            processor=TTS_PROCESSOR,
            model=None,
            kind="tts_usage",
            value_num=float(len(text)),
        )

    # ---- the hook --------------------------------------------------------

    def on_msg(self, msg) -> None:
        try:
            self._dispatch(msg)
        except Exception:
            logger.exception("tracing failed for msg_type={}", getattr(msg, "msg_type", "?"))

    def _dispatch(self, msg) -> None:
        msg_type = getattr(msg, "msg_type", None)

        if msg_type == "stt_text":
            text = (msg.text or "").strip()
            if not text:
                return
            self._user_words.append(text)
            self._recorder.record_event(
                "transcription",
                self._ts(msg),
                {"text": text, "language": self._lang},
            )
            return

        if msg_type == "tts_text":
            text = (msg.text or "").strip()
            if not text:
                return
            turn = getattr(msg, "turn_idx", None)
            # A new turn index means the previous agent turn ended without an
            # explicit end event (interruption, mostly). Don't let the turns run
            # together into one message.
            if self._agent_turn is not None and turn is not None and turn != self._agent_turn:
                self._flush_agent()
            self._agent_turn = turn
            self._agent_words.append(text)
            return

        if msg_type == "event":
            event = getattr(msg, "event", None)
            if event is None:
                return
            self._on_event(_normalize(event.event_type), self._ts(msg))
            return

        # 'audio' carries only timing we already get from first_tts_audio /
        # end_tts_audio; 'tool_call' cannot occur (no persona defines tools).

    def _on_event(self, kind: str, ts: int) -> None:
        if kind == "flushing":
            # The multiplexer has decided the user stopped talking. This is the
            # start of the clock the user actually feels.
            self._flush_user()
            self._recorder.record_event("user_stopped_speaking", ts)

        elif kind in ("push_to_llm", "llm_started"):
            # Both fire per turn; the first one to arrive owns the timestamp.
            if self._t_llm_start_ns is None:
                self._t_llm_start_ns = ts
                self._recorder.record_event("llm_start", ts)

        elif kind == "first_word":
            # First token out of the LLM => LLM time-to-first-token.
            self._t_first_word_ns = ts
            if self._t_llm_start_ns is not None:
                self._record_ttfb(LLM_PROCESSOR, self._t_llm_start_ns, ts)

        elif kind == "first_tts_audio":
            # First audio byte out of TTS => the bot is now audibly speaking.
            self._recorder.record_event("bot_started_speaking", ts)
            self._recorder.record_event("tts_start", ts)
            if self._t_first_word_ns is not None:
                self._record_ttfb(TTS_PROCESSOR, self._t_first_word_ns, ts)

        elif kind == "end_tts_audio":
            # The agent has stopped making sound — but do NOT close the transcript
            # here. `tts_text` captions lag the audio they describe, so a trailing
            # word can arrive after this event; flushing now splits one sentence
            # across two message rows ("…what did you" / "say?").
            self._recorder.record_event("bot_stopped_speaking", ts)
            self._recorder.record_event("tts_stop", ts)
            self._recorder.record_event("llm_end", ts)
            self._reset_turn()

        elif kind == "end_of_turn":
            # The real turn boundary: the user has finished speaking and the
            # exchange is about to move on. Everything the agent said is now in.
            self._flush_agent()
            self._reset_turn()

        elif kind == "interrupted":
            # Barge-in: whatever the agent had said so far still happened, so keep
            # it, but the turn's stage timings are void.
            self._recorder.record_event("interrupted", ts)
            self._flush_agent()
            self._reset_turn()

        else:
            # Unrecognized upstream event. Record it verbatim rather than dropping
            # it — if gradbot renames or adds one, it shows up in the DB instead of
            # vanishing.
            self._recorder.record_event(kind, ts)

    def _record_ttfb(self, processor: str, start_ns: int, end_ns: int) -> None:
        seconds = (end_ns - start_ns) / 1_000_000_000
        if seconds < 0:
            # Clocks disagreeing means the mapping is wrong, not that the future
            # arrived early. Say so rather than writing a negative latency.
            logger.warning("negative ttfb for {}: {:.3f}s — dropped", processor, seconds)
            return
        self._recorder.record_metric(
            processor=processor, model=None, kind="ttfb", value_num=seconds
        )

    def _reset_turn(self) -> None:
        self._t_llm_start_ns = None
        self._t_first_word_ns = None

    # ---- shutdown --------------------------------------------------------

    def close(self) -> None:
        """Flush partial turns. A caller who hangs up mid-sentence still said it."""
        self._flush_user()
        self._flush_agent()
