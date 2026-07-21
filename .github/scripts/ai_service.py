import os
import time
import logging
import google.genai as genai
import google.genai.types as genai_types
from config import GEMINI_API_KEY, MODEL_HIERARCHY, RPM_LIMITS
from schemas import AgentReviewResult

_log = logging.getLogger(__name__)

current_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
last_request_time = 0.0


def get_rate_limit_delay(model: str) -> float:
    rpm = RPM_LIMITS.get(model, 10)
    return 60.0 / rpm


def derank_model() -> bool:
    global current_model
    if current_model in MODEL_HIERARCHY:
        idx = MODEL_HIERARCHY.index(current_model)
        if idx + 1 < len(MODEL_HIERARCHY):
            old = current_model
            current_model = MODEL_HIERARCHY[idx + 1]
            _log.warning(f"🔻 Deranking model from {old} to {current_model} due to rate limits.")
            return True
    elif MODEL_HIERARCHY:
        old = current_model
        current_model = "gemini-2.5-flash"
        _log.warning(f"🔻 Deranking model from {old} to {current_model}.")
        return True
    return False


def enforce_rate_limit():
    global last_request_time
    delay = get_rate_limit_delay(current_model)
    elapsed = time.time() - last_request_time
    if elapsed < delay:
        wait = delay - elapsed
        _log.info(f"⏳ Rate limiting: waiting {wait:.1f}s before request (model: {current_model})")
        time.sleep(wait)
    last_request_time = time.time()


_client = genai.Client(api_key=GEMINI_API_KEY)


def gemini(system_prompt: str, user_content: str, max_retries: int = 4) -> AgentReviewResult:
    global current_model

    for attempt in range(max_retries + 1):
        enforce_rate_limit()
        try:
            _log.info(f"Sending request using model: {current_model} (attempt {attempt + 1})")
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
            _log.warning(f"Attempt {attempt + 1} failed with model {current_model}: {msg[:200]}")

            is_transient_or_rate_or_notfound = any(
                err in msg.lower()
                for err in ("429", "resource_exhausted", "504", "503", "gateway timeout", "timed out", "timeout", "404", "not found")
            )
            if is_transient_or_rate_or_notfound and derank_model():
                _log.info(f"Retrying immediately with deranked model {current_model}...")
                continue

            if attempt < max_retries:
                wait_sec = 5.0 * (2 ** attempt)
                _log.warning(f"Waiting {wait_sec:.1f}s before retry...")
                time.sleep(wait_sec)
            else:
                raise exc
