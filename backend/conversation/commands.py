"""Deterministic parsing of spoken control commands and yes/no answers.

These run *before* any LLM call so that "NOOR, stop" is instant, works offline and can never be
misread as a new task. Matching is deliberately strict (whole-utterance), so "stop the video on
YouTube" is a task, not a control command.
"""
from __future__ import annotations

import re
from enum import Enum


class Control(str, Enum):
    STOP = "stop"
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    STATUS = "status"
    PROGRESS = "progress"
    REPEAT = "repeat"
    BACK = "back"


_FILLER = re.compile(r"\b(please|kindly|noor|hey|ok|okay|now|right now|for me|just|can you|could you|would you)\b", re.I)

_CONTROL: list[tuple[re.Pattern[str], Control]] = [
    (re.compile(r"stop( that| it| this| talking| everything| speaking| the task)?|enough|be quiet|quiet|shut up|abort|never ?mind|forget it", re.I), Control.STOP),
    (re.compile(r"cancel( that| it| this| the task| everything)?|scrap that|drop it", re.I), Control.CANCEL),
    (re.compile(r"pause( that| it| this| the task)?( for a (moment|second|sec|minute))?|wait( a)?( (moment|second|sec|minute))?|hold on|hang on", re.I), Control.PAUSE),
    (re.compile(r"continue( that| it| please)?|resume|go on|carry on|keep going|proceed|you can continue|go ahead and continue", re.I), Control.RESUME),
    (re.compile(r"what (are you|r you) doing|what'?s (happening|going on)|what is happening|what is going on|status|where are you( at)?", re.I), Control.STATUS),
    (re.compile(r"how much (is )?(left|remaining)|how much longer|how far (along )?are you|how long (will it take|is left)|are you (done|finished)|progress", re.I), Control.PROGRESS),
    (re.compile(r"repeat( that| it)?|say (that|it) again|what did you say|come again|pardon|sorry what", re.I), Control.REPEAT),
    (re.compile(r"go back|back|previous page|navigate back|take me back|one page back", re.I), Control.BACK),
]

_YES = frozenset({'approve', 'save it', 'affirmative', 'sounds good', "that's fine", 'do it', 'yeah', 'approved', 'ok', 'yes do it', 'go ahead', 'yup', 'sure', 'confirm', 'absolutely', 'yep', 'go for it', 'send it', 'yes', 'correct', 'okay', 'thats fine'})
_NO = frozenset({'no', 'negative', 'dont do it', "don't do it", 'not now', "no don't", 'nah', 'do not', 'no thanks', 'nope', "don't", 'dont'})


def normalize(text: str) -> str:
    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    t = _FILLER.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_control(text: str) -> Control | None:
    t = normalize(text)
    if not t:
        return None
    for pattern, control in _CONTROL:
        if pattern.fullmatch(t):
            return control
    return None


def parse_confirmation(text: str) -> str | None:
    t = normalize(text)
    if t in _YES or t.rstrip(" .") in _YES:
        return "yes"
    if t in _NO:
        return "no"
    return None


def is_done_signal(text: str) -> bool:
    return bool(re.fullmatch(r"(i'?m |i am )?(done|finished|logged in|ready|all set|it'?s done|i logged in|i have logged in|completed)( now)?", normalize(text), re.I))
