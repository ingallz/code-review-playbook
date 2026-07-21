import os
import time
import logging
import google.genai as genai
import google.genai.types as genai_types
from config import GEMINI_API_KEY, MODEL_HIERARCHY, RPM_LIMITS
from schemas import AgentReviewResult

_log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
last_request_time = 0.0


def get_rate_limit_delay(model: str) -> float:
    rpm = RPM_LIMITS.get(model, 10)
    return 60.0 / rpm


# ponytail: returns next fallback model without mutating persistent model state across agent runs
def get_next_deranked_model(current_model: str) -> str | None:
    if current_model in MODEL_HIERARCHY:
        idx = MODEL_HIERARCHY.index(current_model)
        if idx + 1 < len(MODEL_HIERARCHY):
            next_model = MODEL_HIERARCHY[idx + 1]
            _log.warning(f"🔻 Deranking model from {current_model} to {next_model} due to rate limits/errors.")
            return next_model
    elif MODEL_HIERARCHY:
        next_model = MODEL_HIERARCHY[0]
        _log.warning(f"🔻 Deranking model from {current_model} to {next_model}.")
        return next_model
    return None


def enforce_rate_limit(model: str):
    global last_request_time
    delay = get_rate_limit_delay(model)
    elapsed = time.time() - last_request_time
    if elapsed < delay:
        wait = delay - elapsed
        _log.info(f"⏳ Rate limiting: waiting {wait:.1f}s before request (model: {model})")
        time.sleep(wait)
    last_request_time = time.time()


_client = genai.Client(api_key=GEMINI_API_KEY)


def gemini(system_prompt: str, user_content: str, agent_name: str = "", max_retries: int = 4) -> AgentReviewResult:
    # ponytail: reset to initial default model for each agent/rule run
    current_model = DEFAULT_MODEL
    prefix = f"[{agent_name}] " if agent_name else ""

    for attempt in range(max_retries + 1):
        enforce_rate_limit(current_model)
        try:
            _log.info(f"{prefix}Sending request using model: {current_model} (attempt {attempt + 1})")
            response = _client.models.generate_content(
                model=current_model,
                contents=user_content,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=AgentReviewResult,
                    http_options=genai_types.HttpOptions(timeout=1_000_000),  # 1000 seconds
                ),
            )
            return AgentReviewResult.model_validate_json(response.text)
        except Exception as exc:
            msg = str(exc)
            _log.warning(f"{prefix}Attempt {attempt + 1} failed with model {current_model}: {msg[:200]}")

            is_transient_or_rate_or_notfound = any(
                err in msg.lower()
                for err in ("429", "resource_exhausted", "504", "503", "gateway timeout", "timed out", "timeout", "404", "not found")
            )
            next_model = get_next_deranked_model(current_model) if is_transient_or_rate_or_notfound else None
            if next_model:
                current_model = next_model
                _log.info(f"{prefix}Retrying immediately with deranked model {current_model}...")
                continue

            if attempt < max_retries:
                wait_sec = 5.0 * (2 ** attempt)
                _log.warning(f"{prefix}Waiting {wait_sec:.1f}s before retry...")
                time.sleep(wait_sec)
            else:
                raise exc
