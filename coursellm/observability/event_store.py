from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Any

MAX_STORED_REQUESTS = 200
MAX_EVENTS_PER_REQUEST = 20

_lock = Lock()
_events_by_request: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
_owners_by_request: dict[str, int | None] = {}


def append_event(payload: dict[str, Any]) -> None:
    request_id = payload.get("request_id")
    if not request_id:
        return

    with _lock:
        if request_id not in _events_by_request:
            _events_by_request[request_id] = []
            _owners_by_request[request_id] = payload.get("user_id")
            if len(_events_by_request) > MAX_STORED_REQUESTS:
                evicted_id, _ = _events_by_request.popitem(last=False)
                _owners_by_request.pop(evicted_id, None)

        events = _events_by_request[request_id]
        events.append(payload)
        if len(events) > MAX_EVENTS_PER_REQUEST:
            del events[: len(events) - MAX_EVENTS_PER_REQUEST]

        _events_by_request.move_to_end(request_id)


def get_events(request_id: str) -> list[dict[str, Any]] | None:
    with _lock:
        events = _events_by_request.get(request_id)
        if events is None:
            return None
        return [dict(event) for event in events]


def get_owner_user_id(request_id: str) -> int | None:
    with _lock:
        return _owners_by_request.get(request_id)


def clear_store() -> None:
    with _lock:
        _events_by_request.clear()
        _owners_by_request.clear()
