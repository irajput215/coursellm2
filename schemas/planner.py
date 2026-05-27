from pydantic import BaseModel, Field


class ProcessEmailsRequest(BaseModel):
    course_name: str = Field(..., min_length=1, examples=["COMP9044"])
    force_refresh: bool = Field(
        default=False,
        description="Re-apply email extraction even when an event already exists",
    )


class StudyPlanRequest(BaseModel):
    course_name: str = Field(..., min_length=1, examples=["COMP9044"])
    weeks: int = Field(default=4, ge=1, le=12)
