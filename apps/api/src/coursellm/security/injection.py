"""Deterministic prompt-injection detection.

``docs/architecture/security.md`` section 2 is the specification. Six signal
classes are scored by documented, independently testable rules and combined into
a single score in ``[0, 1]``; the score maps to one of three verdict levels
(``allow`` / ``sanitize`` / ``refuse``) using the thresholds from settings.

**Detection is advisory; the real defence is structural.** A detector is a
classifier over adversarial natural language, and any threshold that catches
paraphrase also catches legitimate academic phrasing ("explain why someone would
say *ignore your instructions*"). This module exists for telemetry, cheap
refusal, and batch-level abuse detection. Containment lives in the delimited
evidence region (:mod:`coursellm.rag.generation.context`), the permission matrix
(:mod:`coursellm.tools.permissions`) and argument validation
(:mod:`coursellm.tools.registry`) — deleting this module would not change them.

Nothing here performs I/O. :func:`classify` is pure and synchronous. The optional
LLM second opinion is a separate coroutine (:func:`classify_with_classifier`)
that takes an injected callable, may only *escalate* a deterministic verdict, and
leaves that verdict untouched when the classifier fails or answers with
malformed output.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import html
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from coursellm.core.config import Settings

#: The three verdict levels. ``sanitize`` runs retrieval and the model but places
#: the query in the untrusted region; ``refuse`` makes no model call at all.
Level = Literal["allow", "sanitize", "refuse"]

#: The six signal classes, in the document's order.
SIGNAL_CLASSES: tuple[str, ...] = (
    "instruction_override",
    "role_manipulation",
    "system_prompt_exfiltration",
    "encoded_payload",
    "delimiter_escape",
    "tool_coercion",
)

#: Per-class weights. A class contributes its weight once, no matter how many of
#: its rules fire, so a wall of trigger words cannot inflate the score. The six
#: weights sum to more than one on purpose: the score is the sum of the fired
#: classes' weights *clipped to* ``[0, 1]``, so a single high-confidence class
#: (``instruction_override``) already crosses the block threshold while two
#: moderate classes saturate. The clip is what keeps the value a probability-like
#: score rather than an unbounded counter.
CLASS_WEIGHTS: dict[str, float] = {
    "instruction_override": 0.80,
    "delimiter_escape": 0.75,
    "system_prompt_exfiltration": 0.70,
    "role_manipulation": 0.55,
    "tool_coercion": 0.55,
    "encoded_payload": 0.45,
}

#: Defaults mirror :class:`~coursellm.core.config.Settings`; they are used only
#: when :func:`classify` is called without a settings object.
DEFAULT_WARN_THRESHOLD = 0.4
DEFAULT_BLOCK_THRESHOLD = 0.75

#: Classifier confidence at or above which an ``injection`` verdict escalates.
#: The settings object does not yet carry this knob, so it is a module constant
#: here (``security.md`` section 2.1 names it ``INJECTION_LLM_CLASSIFIER_THRESHOLD``).
INJECTION_LLM_CLASSIFIER_THRESHOLD = 0.7

#: The longest excerpt a :class:`Match` may carry. The whole payload is never
#: recorded; observability.md section 6 forbids raw matched text on a span.
MAX_EXCERPT_CHARS = 48

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_UNICODE_TAGS = re.compile(r"[\U000e0000-\U000e007f]")
_HTML_ENTITY = re.compile(r"&#x?[0-9a-fA-F]+;")
_BASE64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")
_HEX_TOKEN = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")

_OVERRIDE_VERBS = (
    r"(?:ignore|disregard|forget|override|overrule|bypass|discard|dismantle|"
    r"set\s+aside|cast\s+aside|pay\s+no\s+attention\s+to)"
)
_INSTRUCTION_NOUNS = (
    r"(?:instructions?|rules?|guidelines?|directives?|directions?|commands?|orders?|"
    r"constraints?|training|prompts?|guidance)"
)
_DISCLOSE_VERBS = (
    r"(?:repeat|print|show|reveal|display|output|expose|leak|dump|return|share|recite|"
    r"echo|tell\s+me|list|summari[sz]e|translate)"
)
_PROMPT_NOUNS = (
    r"(?:system\s+prompt|system\s+message|initial\s+instructions?|original\s+instructions?|"
    r"hidden\s+prompt|your\s+instructions?|your\s+prompt|instructions?\s+(?:above|before)|"
    r"prompt\s+(?:above|before)|everything\s+above|text\s+above|the\s+prompt)"
)
_IMPERATIVE_VERBS = (
    r"(?:call|invoke|use|run|execute|send|delete|remove|fetch|email|download|upload|"
    r"visit|browse|query|drop|grant|create|write|update)"
)
_CAPABILITY_NOUNS = (
    r"(?:tools?|sql|shell|command\s+line|commands?|e-?mails?|urls?|https?|files?|"
    r"filesystem|databases?|dbs?|apis?|endpoints?|browser|internet|network|"
    r"http\s+request|credentials?)"
)
#: Every tool name the agent layer knows, including the three denied capabilities.
#: Naming any of them in a user turn is a tool-coercion signal even without an
#: imperative verb.
_TOOL_NAMES: tuple[str, ...] = (
    "search_documents",
    "search_course",
    "search_knowledge_graph",
    "search_books",
    "search_web_sources",
    "get_student_progress",
    "create_quiz",
    "evaluate_answer",
    "update_learning_plan",
    "get_recommendations",
    "send_email",
    "delete_document",
    "run_sql",
)

_ROLE_WORDS = (
    r"(?:unrestricted|unfiltered|uncensored|jailbroken|jailbreak|developer|admin|"
    r"administrator|root|superuser|system|dan|different|new)"
)

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "instruction_override": (
        re.compile(
            rf"\b{_OVERRIDE_VERBS}\b(?:\s+[A-Za-z']+){{0,4}}?\s+"
            rf"(?:\w+\s+){{0,2}}?{_INSTRUCTION_NOUNS}\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bnew\s+instructions?\s*[:=\-]", re.IGNORECASE),
        re.compile(r"\bfrom\s+now\s+on\b", re.IGNORECASE),
        re.compile(r"\bdo\s+not\s+(?:follow|obey|listen\s+to)\b", re.IGNORECASE),
    ),
    "role_manipulation": (
        re.compile(
            rf"\byou\s+are\s+(?:now\s+|no\s+longer\s+)?(?:a|an|the)\s+{_ROLE_WORDS}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:act|behave|respond|answer|pretend)\s+(?:as|to\s+be)\s+"
            rf"(?:a|an|the)?\s*{_ROLE_WORDS}\b",
            re.IGNORECASE,
        ),
        re.compile(r"(?m)^\s*(?:system|assistant|developer)\s*:", re.IGNORECASE),
        re.compile(r"\b(?:enable|enter|switch\s+to)\s+(?:developer|debug|god)\s+mode\b", re.I),
        re.compile(r"\byour\s+(?:new\s+)?(?:role|persona)\s+is\b", re.IGNORECASE),
    ),
    "system_prompt_exfiltration": (
        re.compile(
            rf"\b{_DISCLOSE_VERBS}\b(?:\s+[A-Za-z']+){{0,5}}?\s+"
            rf"(?:\w+\s+){{0,2}}?{_PROMPT_NOUNS}\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:what|which)\s+(?:is|are|were)\s+(?:your|the)\s+"
            r"(?:system\s+prompt|instructions?|prompt)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bprint\s+everything\s+above\b", re.IGNORECASE),
    ),
    "delimiter_escape": (
        re.compile(r"</?untrusted_evidence", re.IGNORECASE),
        re.compile(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>", re.I),
        re.compile(r"\[/?INST\]|<<SYS>>|<</SYS>>"),
        re.compile(r"<\|(?:start_header_id|end_header_id)\|>", re.IGNORECASE),
        re.compile(r"(?m)^\s*#{2,}\s*(?:system|assistant|developer|human)\s*:", re.IGNORECASE),
    ),
    "tool_coercion": (
        re.compile(rf"\b(?:{'|'.join(re.escape(name) for name in _TOOL_NAMES)})\b", re.IGNORECASE),
        re.compile(
            rf"(?:^|[.!?;:]\s*|\bplease\s+|\bnow\s+|\bthen\s+|\byou\s+must\s+|\b"
            rf"i\s+need\s+you\s+to\s+){_IMPERATIVE_VERBS}\b[^.!?\n]{{0,48}}?"
            rf"\b{_CAPABILITY_NOUNS}\b",
            re.IGNORECASE,
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class Match:
    """One rule hit.

    ``excerpt`` is a short, control-character-stripped fragment. It is never the
    whole payload, and :func:`redact_excerpt` masks anything credential-shaped
    before the fragment is recorded.
    """

    signal_class: str
    span: tuple[int, int]
    excerpt: str


class InjectionVerdict(BaseModel):
    """The detector's explainable verdict vector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: float
    classes: list[str]
    level: Level
    matches: list[Match]
    classifier_escalated: bool = False


class ClassifierVerdict(BaseModel):
    """The strict JSON shape the optional second-opinion model must return.

    The wire field is ``class`` (``security.md`` section 2.2); it is aliased to
    ``signal_class`` because ``class`` is a Python keyword.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    injection: bool
    confidence: float
    signal_class: str = Field(default="llm_classifier", alias="class")


#: Signature of the injected classifier callable.
Classifier = Callable[[str], Awaitable[str | None]]


def level_for_score(score: float, *, warn: float, block: float) -> Level:
    """Map a score to a verdict, taking the stricter level at a boundary.

    A score exactly at ``warn`` sanitises; exactly at ``block`` refuses. The
    comparison is ``>=`` rather than ``>`` so a threshold is inclusive: the
    configuration value is the lowest score that triggers the level.
    """
    if score >= block:
        return "refuse"
    if score >= warn:
        return "sanitize"
    return "allow"


def classify(text: str, *, settings: Settings | None = None) -> InjectionVerdict:
    """Score ``text`` against the six deterministic signal classes.

    Pure and synchronous. The same input always produces the same verdict; no
    clock, no randomness and no I/O participate.
    """
    warn, block = _thresholds(settings)
    matches = _collect_matches(text)
    fired = sorted({match.signal_class for match in matches})
    score = min(1.0, sum(CLASS_WEIGHTS[name] for name in fired))
    level = level_for_score(score, warn=warn, block=block)
    return InjectionVerdict(
        score=round(score, 6),
        classes=fired,
        level=level,
        matches=matches,
    )


async def classify_with_classifier(
    text: str,
    *,
    settings: Settings | None = None,
    classifier: Classifier,
) -> InjectionVerdict:
    """Run the deterministic layer, then an optional model second opinion.

    The classifier may only **escalate**: a deterministic ``allow`` or
    ``sanitize`` can become ``refuse``, never the other way round. A classifier
    that raises, times out, or returns text that is not a strict
    ``{"injection": bool, "confidence": float, "class": str}`` object leaves the
    deterministic verdict exactly as it was — a broken classifier means "no
    second opinion", never "allow" and never "refuse".
    """
    deterministic = classify(text, settings=settings)
    if settings is not None and not settings.injection_llm_classifier_enabled:
        return deterministic
    try:
        raw = await classifier(text)
    except Exception:  # a classifier failure must not change the verdict
        return deterministic
    parsed = parse_classifier_payload(raw)
    if parsed is None:
        return deterministic
    return apply_classifier(deterministic, parsed, settings=settings)


def apply_classifier(
    verdict: InjectionVerdict,
    payload: ClassifierVerdict,
    *,
    settings: Settings | None = None,
) -> InjectionVerdict:
    """Escalate ``verdict`` from a parsed classifier opinion, or return it unchanged."""
    if not payload.injection or payload.confidence < INJECTION_LLM_CLASSIFIER_THRESHOLD:
        return verdict
    _warn, block = _thresholds(settings)
    if verdict.level == "refuse":
        return verdict
    classes = list(verdict.classes)
    if payload.signal_class not in classes:
        classes.append(payload.signal_class)
    return verdict.model_copy(
        update={
            "score": max(verdict.score, block),
            "classes": classes,
            "level": "refuse",
            "classifier_escalated": True,
        }
    )


def parse_classifier_payload(raw: str | None) -> ClassifierVerdict | None:
    """Parse the classifier's strict JSON verdict, or ``None`` if it is not one.

    Free text is never interpreted as a verdict. A response that is not a JSON
    object with exactly the declared fields is discarded.
    """
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    try:
        return ClassifierVerdict.model_validate(decoded)
    except Exception:
        return None


def redact_excerpt(value: str) -> str:
    """Shorten and neutralise a matched fragment for safe recording."""
    collapsed = " ".join(value.split())
    collapsed = "".join(character if character.isprintable() else " " for character in collapsed)
    collapsed = collapsed.strip()
    if len(collapsed) <= MAX_EXCERPT_CHARS:
        return collapsed
    return collapsed[: MAX_EXCERPT_CHARS - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _thresholds(settings: Settings | None) -> tuple[float, float]:
    if settings is None:
        return DEFAULT_WARN_THRESHOLD, DEFAULT_BLOCK_THRESHOLD
    return settings.injection_warn_threshold, settings.injection_block_threshold


def _collect_matches(text: str) -> list[Match]:
    matches: list[Match] = []
    for signal_class in SIGNAL_CLASSES:
        if signal_class == "encoded_payload":
            continue
        for pattern in _PATTERNS[signal_class]:
            for found in pattern.finditer(text):
                matches.append(
                    Match(
                        signal_class=signal_class,
                        span=(found.start(), found.end()),
                        excerpt=redact_excerpt(found.group(0)),
                    )
                )

    encoded, decoded_classes = _encoded_signals(text)
    if encoded is not None:
        matches.append(encoded)
        for signal_class, span, excerpt in decoded_classes:
            matches.append(
                Match(signal_class=signal_class, span=span, excerpt=redact_excerpt(excerpt))
            )
    return matches


def _encoded_signals(text: str) -> tuple[Match | None, list[tuple[str, tuple[int, int], str]]]:
    """Detect an encoded payload and any signal class its decoded form fires.

    The decode-and-recheck step is the point: a long base64 blob that decodes to
    a normal lecture sentence is not flagged, while one whose decoded form
    contains an override is.
    """
    decoded_children: list[tuple[str, tuple[int, int], str]] = []
    found_encoding: tuple[int, int, str] | None = None

    invisible = _ZERO_WIDTH.search(text) or _UNICODE_TAGS.search(text)
    if invisible is not None:
        found_encoding = (invisible.start(), invisible.end(), invisible.group(0))

    entity = _HTML_ENTITY.search(text)
    if entity is not None:
        decoded = html.unescape(entity.group(0))
        found_encoding = found_encoding or (entity.start(), entity.end(), entity.group(0))
        decoded_children.extend(_recheck(decoded, span=(entity.start(), entity.end())))

    for token_re, decoder in ((_BASE64_TOKEN, _try_base64), (_HEX_TOKEN, _try_hex)):
        candidate = token_re.search(text)
        if candidate is None:
            continue
        decoded_token = decoder(candidate.group(0))
        if decoded_token is None:
            continue
        children = _recheck(decoded_token, span=(candidate.start(), candidate.end()))
        if children:
            found_encoding = found_encoding or (
                candidate.start(),
                candidate.end(),
                candidate.group(0),
            )
            decoded_children.extend(children)

    rotated = codecs.decode(text, "rot_13")
    if rotated != text:
        children = _recheck(rotated, span=(0, len(text)))
        if children:
            found_encoding = found_encoding or (0, len(text), text)
            decoded_children.extend(children)

    if found_encoding is None:
        return None, []
    start, end, raw = found_encoding
    return Match("encoded_payload", (start, end), redact_excerpt(raw)), decoded_children


def _recheck(decoded: str, *, span: tuple[int, int]) -> list[tuple[str, tuple[int, int], str]]:
    """Return the raw-text classes the *decoded* form fires, with the raw span."""
    if not decoded.strip():
        return []
    found: list[tuple[str, tuple[int, int], str]] = []
    for signal_class in SIGNAL_CLASSES:
        if signal_class == "encoded_payload":
            continue
        for pattern in _PATTERNS[signal_class]:
            match = pattern.search(decoded)
            if match is not None:
                found.append((signal_class, span, match.group(0)))
                break
    return found


def _try_base64(token: str) -> str | None:
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None
    return _decode_text(raw)


def _try_hex(token: str) -> str | None:
    try:
        raw = bytes.fromhex(token)
    except ValueError:
        return None
    return _decode_text(raw)


def _decode_text(raw: bytes) -> str | None:
    """Decode bytes as text only when the result is plausible prose.

    Random binary decodes to mostly non-printable characters and is rejected, so
    a hex-looking identifier does not become a false positive.
    """
    if not raw:
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(1 for character in decoded if character.isprintable() or character.isspace())
    if printable / len(decoded) < 0.9:
        return None
    letters = sum(1 for character in decoded if character.isalpha())
    if letters < 4:
        return None
    # NFKC-normalise homoglyphs so a fullwidth or mathematical-alphabet override
    # decodes to the ASCII form the rules match.
    return unicodedata.normalize("NFKC", decoded)


__all__ = [
    "CLASS_WEIGHTS",
    "DEFAULT_BLOCK_THRESHOLD",
    "DEFAULT_WARN_THRESHOLD",
    "INJECTION_LLM_CLASSIFIER_THRESHOLD",
    "SIGNAL_CLASSES",
    "ClassifierVerdict",
    "InjectionVerdict",
    "Level",
    "Match",
    "apply_classifier",
    "classify",
    "classify_with_classifier",
    "level_for_score",
    "parse_classifier_payload",
    "redact_excerpt",
]
