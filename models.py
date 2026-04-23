from pydantic import BaseModel
from typing import Optional


class HealthResponse(BaseModel):
    status: str
    app_name: str
    version: str
    env: str


class ChatRequest(BaseModel):
    prompt: str


class ChatResponse(BaseModel):
    answer: str
    latency_ms: Optional[float] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    total_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
