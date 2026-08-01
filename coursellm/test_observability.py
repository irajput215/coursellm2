"""Smoke tests for Stage E Phase 1 observability."""

from fastapi.testclient import TestClient

from main import app
from observability.context import reset_context, set_request_id, set_user_id
from observability.event_store import append_event, clear_store, get_events
from observability.tracing.langfuse_client import (
    NoOpSpan,
    is_tracing_enabled,
    log_pipeline_event,
    trace_span,
)


def test_request_id_middleware():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


def test_request_id_propagation_from_header():
    client = TestClient(app)
    custom_id = "test-request-id-12345"
    response = client.get("/", headers={"X-Request-ID": custom_id})
    assert response.headers["X-Request-ID"] == custom_id


def test_trace_span_noop_when_disabled():
    reset_context()
    set_request_id("req-test-1")
    with trace_span("test_span") as span:
        assert isinstance(span, NoOpSpan)
        span.update(output={"ok": True})


def test_event_store_roundtrip():
    clear_store()
    append_event(
        {
            "request_id": "req-store-1",
            "user_id": 1,
            "event": "semantic_search",
            "semantic_chunks": [1, 2],
        }
    )
    events = get_events("req-store-1")
    assert events is not None
    assert len(events) == 1
    assert events[0]["event"] == "semantic_search"


def test_get_observability_events_requires_auth():
    clear_store()
    append_event(
        {
            "request_id": "req-auth-1",
            "user_id": 1,
            "event": "hybrid_retrieval",
            "final_chunks": [3, 4],
        }
    )
    client = TestClient(app)
    response = client.get("/observability/events/req-auth-1")
    assert response.status_code == 401


def run_smoke_tests():
    test_request_id_middleware()
    test_request_id_propagation_from_header()
    test_trace_span_noop_when_disabled()
    test_event_store_roundtrip()
    test_get_observability_events_requires_auth()
    print("All observability smoke tests passed.")


if __name__ == "__main__":
    run_smoke_tests()

    client = TestClient(app)
    response = client.get("/")
    print(f"Status: {response.status_code}")
    print(f"X-Request-ID: {response.headers.get('X-Request-ID')}")
    print(f"Tracing enabled: {is_tracing_enabled()}")

    reset_context()
    set_request_id("manual-test")
    set_user_id(1)
    with trace_span("ask") as span:
        print(f"Span type: {type(span).__name__}")
    log_pipeline_event("hybrid_retrieval", query="test", final_chunks=[1, 2])
    stored = get_events("manual-test")
    print(f"Stored events: {len(stored or [])}")
    print("Observability manual smoke test complete.")
