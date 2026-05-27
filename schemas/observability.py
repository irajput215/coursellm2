from typing import Any

from pydantic import BaseModel, Field


class PipelineEvent(BaseModel):
    request_id: str | None = None
    event: str
    user_id: int | None = None
    course_id: int | None = None
    trace_id: str | None = None
    model_config = {"extra": "allow"}


class PipelineEventsResponse(BaseModel):
    request_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
