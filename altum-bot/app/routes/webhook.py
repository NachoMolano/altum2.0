import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, Response, BackgroundTasks, Query
from starlette.responses import PlainTextResponse

from config import settings
from app.core.agent import process_message

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook/instagram")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Meta webhook verification endpoint."""
    if hub_mode == "subscribe" and hub_verify_token == settings.INSTAGRAM_VERIFY_TOKEN:
        logger.info("[WEBHOOK] Verification successful")
        return PlainTextResponse(content=hub_challenge)

    logger.warning("[WEBHOOK] Verification failed: mode=%s", hub_mode)
    return Response(status_code=403)


@router.post("/webhook/instagram")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Instagram messages via webhook."""
    # 1. Validate signature
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_signature(body, signature):
        logger.warning("[WEBHOOK] Invalid signature")
        return Response(status_code=403)

    # 2. Parse payload
    payload = await request.json()

    for entry in payload.get("entry", []):
        logger.info("[WEBHOOK] entry keys=%s changes_fields=%s", list(entry.keys()), [c.get("field") for c in entry.get("changes", [])])
        # Instagram sends events under "changes", fallback to "messaging"
        events = [c.get("value", {}) for c in entry.get("changes", []) if c.get("field") == "messages"]
        events += entry.get("messaging", [])

        for event in events:
            sender_id = event.get("sender", {}).get("id")
            recipient_id = event.get("recipient", {}).get("id")
            message = event.get("message", {})
            text = message.get("text")
            message_id = message.get("mid")
            logger.info("[WEBHOOK] sender=%s recipient=%s text=%s event_keys=%s", sender_id, recipient_id, text, list(event.keys()))

            # 3. Skip echo messages
            if not sender_id or not text or sender_id == recipient_id:
                continue

            logger.info("[WEBHOOK] user_id=%s text_preview=%s", sender_id, text[:50])

            # 4. Process in background (Meta requires <200ms response)
            background_tasks.add_task(process_message, sender_id, text, message_id)

    # 5. Return 200 immediately
    return Response(status_code=200)


def _verify_signature(body: bytes, signature: str) -> bool:
    """Validate HMAC-SHA256 signature from Meta."""
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        settings.INSTAGRAM_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)
