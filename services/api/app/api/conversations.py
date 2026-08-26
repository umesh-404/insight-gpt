"""Server-side conversation store (doc 06 §3.1).

``/ask`` mints a ``conversation_id`` for every turn; without somewhere to put it
the id is meaningless and the client's history sidebar has nothing to read. This
module is that somewhere: a process-local, thread-safe, bounded store of
conversations keyed by the owning user's JWT ``sub``.

It is deliberately in-memory (demo scope, mirroring ``auth/store.py``): the
public functions are the seam a Postgres-backed implementation slots into
without touching the routers. Access is guarded by a lock because the ``/ask``
path runs the engine in a threadpool, so turns can land concurrently.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..engine.envelope import AnswerEnvelope

#: Hard cap on retained conversations; the least recently updated is evicted.
MAX_CONVERSATIONS = 200

#: Longest title kept when deriving one from the first question.
TITLE_MAX_CHARS = 60

#: Longest title a user may set by hand. Longer input is rejected by the router
#: rather than silently truncated, so the client can say why.
TITLE_INPUT_MAX_CHARS = 120

Role = Literal["user", "assistant"]


class Message(BaseModel):
    """One turn-half: the user's question or the assistant's answer."""

    id: str
    role: Role
    content: str
    #: Only assistant messages carry the envelope that produced them.
    envelope: AnswerEnvelope | None = None
    created_at: datetime


class Turn(BaseModel):
    """A question paired with the answer it produced (client render unit)."""

    id: str
    question: str
    envelope: AnswerEnvelope | None = None
    created_at: datetime


class Conversation(BaseModel):
    """A full transcript, as returned by ``GET /conversations/{id}``."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[Message] = Field(default_factory=list)
    turns: list[Turn] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationPage(BaseModel):
    items: list[ConversationSummary]
    total: int
    limit: int
    offset: int


class _Entry(BaseModel):
    """Internal record: the transcript plus its owner (never serialized out)."""

    user_id: str
    id: str
    title: str
    #: True once the owner renamed the thread. A user-chosen title outranks
    #: anything derivable from a question, so later turns must never clobber it.
    title_is_custom: bool = False
    created_at: datetime
    updated_at: datetime
    messages: list[Message] = Field(default_factory=list)


_LOCK = threading.RLock()
# Keyed by ``(user_id, conversation_id)`` so two users can never collide on the
# same id — a guessed or replayed id lands in the guesser's own namespace and
# leaves the real owner's transcript untouched.
# Insertion-ordered by *creation*; eviction pops the oldest entry.
_CONVERSATIONS: OrderedDict[tuple[str, str], _Entry] = OrderedDict()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)


def derive_title(question: str) -> str:
    """First question, collapsed and truncated, as the conversation's title."""
    text = " ".join(question.split()).strip()
    if not text:
        return "Untitled conversation"
    if len(text) <= TITLE_MAX_CHARS:
        return text
    return text[: TITLE_MAX_CHARS - 1].rstrip() + "…"


def normalize_title(raw: str) -> str:
    """Collapse a user-supplied title's whitespace.

    Returns ``""`` when nothing usable remains — the caller turns that into a
    400 rather than storing a blank row the sidebar cannot label.
    """
    return " ".join(raw.split()).strip()


def _turns(messages: list[Message]) -> list[Turn]:
    """Pair each user message with the assistant reply that followed it."""
    turns: list[Turn] = []
    pending: Message | None = None
    for msg in messages:
        if msg.role == "user":
            if pending is not None:
                turns.append(
                    Turn(id=pending.id, question=pending.content, created_at=pending.created_at)
                )
            pending = msg
            continue
        question = pending.content if pending is not None else ""
        turns.append(
            Turn(
                id=msg.id,
                question=question,
                envelope=msg.envelope,
                created_at=msg.created_at,
            )
        )
        pending = None
    if pending is not None:
        turns.append(
            Turn(id=pending.id, question=pending.content, created_at=pending.created_at)
        )
    return turns


def _view(entry: _Entry) -> Conversation:
    """Deep-copy an entry into the public model so callers cannot mutate state."""
    messages = [m.model_copy(deep=True) for m in entry.messages]
    return Conversation(
        id=entry.id,
        title=entry.title,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        messages=messages,
        turns=_turns(messages),
    )


def _summary(entry: _Entry) -> ConversationSummary:
    return ConversationSummary(
        id=entry.id,
        title=entry.title,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        message_count=len(entry.messages),
    )


def append_turn(
    *,
    user_id: str,
    conversation_id: str,
    question: str,
    message_id: str,
    envelope: AnswerEnvelope,
) -> Conversation:
    """Record a question + its answer, creating the conversation when needed.

    Conversations are scoped to the caller, so an id that another user already
    holds starts a *separate* conversation here rather than joining — or
    overwriting — theirs.
    """
    now = _now()
    key = (user_id, conversation_id)
    with _LOCK:
        entry = _CONVERSATIONS.get(key)
        if entry is None:
            entry = _Entry(
                user_id=user_id,
                id=conversation_id,
                title=derive_title(question),
                created_at=now,
                updated_at=now,
            )
            _CONVERSATIONS[key] = entry
            _evict_locked()
        elif not entry.title_is_custom and not entry.title.strip():
            # Only ever re-derive a title the user has not chosen. A renamed
            # thread keeps its name for every subsequent turn.
            entry.title = derive_title(question)
        entry.messages.append(
            Message(
                id=new_id("m"),
                role="user",
                content=question,
                created_at=now,
            )
        )
        entry.messages.append(
            Message(
                id=message_id,
                role="assistant",
                content=envelope.answer,
                envelope=envelope.model_copy(deep=True),
                created_at=now,
            )
        )
        entry.updated_at = now
        return _view(entry)


def _evict_locked() -> None:
    """Drop the oldest conversations until the store is within its cap."""
    while len(_CONVERSATIONS) > MAX_CONVERSATIONS:
        _CONVERSATIONS.popitem(last=False)


def list_conversations(user_id: str, *, limit: int = 20, offset: int = 0) -> ConversationPage:
    """The caller's conversations, most recently updated first."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    with _LOCK:
        owned = [e for e in _CONVERSATIONS.values() if e.user_id == user_id]
        owned.sort(key=lambda e: e.updated_at, reverse=True)
        window = owned[offset : offset + limit]
        return ConversationPage(
            items=[_summary(e) for e in window],
            total=len(owned),
            limit=limit,
            offset=offset,
        )


def get_conversation(user_id: str, conversation_id: str) -> Conversation | None:
    """The full transcript, or ``None`` when missing or owned by someone else."""
    with _LOCK:
        entry = _CONVERSATIONS.get((user_id, conversation_id))
        return _view(entry) if entry is not None else None


def rename_conversation(
    user_id: str, conversation_id: str, title: str
) -> ConversationSummary | None:
    """Set a user-chosen title, or ``None`` when missing or owned by someone else.

    ``title`` must already be normalized (see :func:`normalize_title`). The
    lookup is keyed by ``(user_id, id)`` exactly like the read path, so another
    user's id is indistinguishable from one that does not exist.

    ``updated_at`` is deliberately untouched: it records the last *turn*, which
    is what the sidebar orders by, and renaming a thread should not jump it to
    the top of the list.
    """
    with _LOCK:
        entry = _CONVERSATIONS.get((user_id, conversation_id))
        if entry is None:
            return None
        entry.title = title
        entry.title_is_custom = True
        return _summary(entry)


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    """Drop a conversation and its turns. ``False`` when there was none to drop."""
    with _LOCK:
        return _CONVERSATIONS.pop((user_id, conversation_id), None) is not None


def reset() -> None:
    """Drop every stored conversation (used by tests)."""
    with _LOCK:
        _CONVERSATIONS.clear()
