import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)
course_id_var: ContextVar[int | None] = ContextVar("course_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_request_id() -> str:
    return str(uuid.uuid4())


def get_request_id() -> str | None:
    return request_id_var.get()


def set_request_id(value: str) -> None:
    request_id_var.set(value)


def get_user_id() -> int | None:
    return user_id_var.get()


def set_user_id(value: int | None) -> None:
    user_id_var.set(value)


def get_course_id() -> int | None:
    return course_id_var.get()


def set_course_id(value: int | None) -> None:
    course_id_var.set(value)


def get_trace_id() -> str | None:
    return trace_id_var.get()


def set_trace_id(value: str | None) -> None:
    trace_id_var.set(value)


def reset_context() -> None:
    request_id_var.set(None)
    user_id_var.set(None)
    course_id_var.set(None)
    trace_id_var.set(None)
