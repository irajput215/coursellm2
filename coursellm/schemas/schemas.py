from pydantic import BaseModel

class PromptRequest(BaseModel):
    prompt: str

class ResponseOut(BaseModel):
    id: int
    prompt: str
    response: str