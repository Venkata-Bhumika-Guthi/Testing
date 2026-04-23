from fastapi import APIRouter, HTTPException, BackgroundTasks

from ..config import settings
from ..models import ChatRequest, ChatResponse
from ..llm.client import generate_answer
from ..telemetry.datadog_client import record_chat_observation

router = APIRouter(tags=["chat"], prefix="/api")

INPUT_PRICE_PER_M = 0.30
OUTPUT_PRICE_PER_M = 2.50


def _estimate_cost_usd(tokens_in: int, tokens_out: int) -> float:
    cost_in = (tokens_in / 1_000_000) * INPUT_PRICE_PER_M
    cost_out = (tokens_out / 1_000_000) * OUTPUT_PRICE_PER_M
    return round(cost_in + cost_out, 6)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, background_tasks: BackgroundTasks) -> ChatResponse:
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt must not be empty.")

    model_name = settings.gemini_model_name

    # Initialize for the exception path (so variables exist even if call fails early)
    latency_ms = 0.0
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0

    try:
        # Call the LLM client to get the answer
        answer_text, latency_ms, tokens_in, tokens_out = generate_answer(prompt)

        tokens_in = tokens_in or 0
        tokens_out = tokens_out or 0
        total_tokens = tokens_in + tokens_out
        cost_usd = _estimate_cost_usd(tokens_in, tokens_out)

        response = ChatResponse(
            answer=answer_text,
            latency_ms=latency_ms,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            total_tokens=total_tokens,
            estimated_cost_usd=cost_usd,
        )

        # New enriched telemetry signature
        background_tasks.add_task(
            record_chat_observation,
            prompt=prompt,
            answer=answer_text,
            model=model_name,
            latency_ms=latency_ms,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            status="success",
            error_type=None,
            user_id=None,
            safety_blocked=False,
            extra_context={"source": "cloud_run"},
        )

        return response

    except Exception as e:
        # On error, per instructions:
        # - answer=None
        # - status="error"
        # - error_type="vertex_error"
        # - tokens_output=0
        # - total_tokens=tokens_input
        tokens_in = tokens_in or 0
        total_tokens = tokens_in

        background_tasks.add_task(
            record_chat_observation,
            prompt=prompt,
            answer=None,
            model=model_name,
            latency_ms=latency_ms,
            tokens_input=tokens_in,
            tokens_output=0,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            status="error",
            error_type="vertex_error",
            user_id=None,
            safety_blocked=False,
            extra_context={"exception": type(e).__name__},
        )

        raise HTTPException(status_code=500, detail=f"Error calling Gemini: {e}")
