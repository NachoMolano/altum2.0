import asyncio
import logging

from config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]


async def chat_completion(
    messages: list[dict],
    system: str,
    max_tokens: int = 800,
) -> str:
    """
    Unified LLM interface. Returns the model's text response.
    Reads settings.LLM_PROVIDER to pick Claude or Gemini.
    """
    provider = settings.LLM_PROVIDER.lower()

    for attempt in range(MAX_RETRIES):
        try:
            if provider == "claude":
                return await _call_claude(messages, system, max_tokens)
            elif provider == "gemini":
                return await _call_gemini(messages, system, max_tokens)
            else:
                raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_SECONDS[attempt]
                logger.warning("[LLM] Attempt %d failed (%s), retrying in %ds...", attempt + 1, e, wait)
                await asyncio.sleep(wait)
            else:
                logger.error("[LLM] All %d attempts failed for provider=%s", MAX_RETRIES, provider)
                raise


async def _call_claude(messages: list[dict], system: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    text = response.content[0].text
    logger.info("[LLM] provider=claude tokens_aprox=%d", len(text))
    return text


async def _call_gemini(messages: list[dict], system: str, max_tokens: int) -> str:
    import google.generativeai as genai

    genai.configure(api_key=settings.GOOGLE_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-preview-05-20",
        system_instruction=system,
        generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens),
    )

    # Convert messages to Gemini format
    history = []
    for msg in messages[:-1]:
        role = "model" if msg["role"] == "assistant" else "user"
        history.append({"role": role, "parts": [msg["content"]]})

    chat = model.start_chat(history=history)
    last_msg = messages[-1]["content"] if messages else ""
    response = await asyncio.to_thread(chat.send_message, last_msg)
    text = response.text
    logger.info("[LLM] provider=gemini tokens_aprox=%d", len(text))
    return text
