
import time
from typing import Tuple

import vertexai
from vertexai.generative_models import GenerativeModel

from ..config import settings


# We lazily initialize the Vertex AI client & model the first time we need it
_model: GenerativeModel | None = None


def get_model() -> GenerativeModel:
    global _model
    if _model is None:
        vertexai.init(
            project=settings.gcp_project_id,
            location=settings.gcp_location,
        )
        _model = GenerativeModel(settings.gemini_model_name)
    return _model


def generate_answer(prompt: str) -> Tuple[str, float, int, int]:
    """
    Call Gemini via Vertex AI and return:
      - answer text
      - latency in ms
      - input token count
      - output token count

    We'll use these for observability (latency, tokens, cost estimates).
    """
    model = get_model()

    start = time.perf_counter()
    response = model.generate_content(prompt)
    latency_ms = (time.perf_counter() - start) * 1000.0

    # Extract text safely
    try:
        candidate = response.candidates[0]
        parts = getattr(candidate, "content", candidate).parts
        text_chunks = []
        for part in parts:
            if hasattr(part, "text"):
                text_chunks.append(part.text)
        answer_text = "\n".join(text_chunks).strip() or "[No text returned from model]"
    except Exception:
        # Fallback, just in case the structure changes
        answer_text = str(response)

    # Usage metadata for tokens
    usage = getattr(response, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0

    return answer_text, latency_ms, input_tokens, output_tokens
