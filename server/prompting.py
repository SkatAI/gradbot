"""System-instruction assembly and output-language policy.

Gradbot's `SessionConfig` has exactly one text field — `instructions`. There is
no context-seeding API (sceance's `opening_messages`) and no way to speak a fixed
line without the LLM (sceance's `static_greeting`). So everything the agent needs
to know, including how to open the conversation, has to be assembled into that
one string. That's what this module does.

Kept separate from `agent.py` (which builds the gradbot objects) so the prompt
policy can be unit-tested without importing gradbot.
"""

from __future__ import annotations

from personas import Persona

VOICE_APPENDIX = """

— Voice —
You are speaking out loud on a phone call. Never use emoji, markdown, bullet
points, headings, or any formatting that cannot be spoken. Keep replies short —
one or two sentences unless the user asks for more.
"""

# Map persona `lang` codes to language names for the output-language directive.
_LANG_NAMES = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
}


def _language_directive(lang: str) -> str:
    """Pin the persona's spoken language so a user speaking something else can't
    flip it. `lang` only steers STT/TTS; the LLM needs telling separately."""
    name = _LANG_NAMES.get(lang.split("-")[0].lower(), "English")
    return (
        f"\n\n— Language —\n"
        f"Always speak {name}. Every response must be in {name}, regardless of "
        f"earlier context or anything the user says. If the user speaks another "
        f"language, gently continue in {name}."
    )


def _first_turn_directive(greeting: str) -> str:
    """Tell the agent how to open.

    In sceance the opening was a `static_greeting` spoken straight to TTS — no
    LLM call, so no cold-start dead air. Gradbot cannot do that (there is no
    speak-this-text API), so the agent opens with a real generated turn and we
    ask it to say the line verbatim. Whether it complies, and what that first
    turn costs in latency, is one of the things this app exists to measure.
    """
    return (
        f"\n\n— First turn —\n"
        f"You speak first, before the user says anything. Open with exactly this "
        f"line, word for word, and nothing else:\n"
        f"{greeting}\n"
        f"Then wait for the user to reply."
    )


def build_system_instruction(persona: Persona) -> str:
    """The complete `SessionConfig.instructions` string for a persona."""
    return (
        persona.system_prompt
        + VOICE_APPENDIX
        + _language_directive(persona.lang)
        + _first_turn_directive(persona.greeting)
    )
