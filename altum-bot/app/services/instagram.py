import asyncio
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.instagram.com/v25.0/me/messages"
MAX_MESSAGE_LENGTH = 900


def _split_message(text: str) -> list[str]:
    """Split text into chunks of at most MAX_MESSAGE_LENGTH chars, breaking at sentence/paragraph boundaries."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= MAX_MESSAGE_LENGTH:
            chunks.append(remaining)
            break

        cut = MAX_MESSAGE_LENGTH
        # Try to break at paragraph
        idx = remaining.rfind("\n\n", 0, cut)
        if idx == -1:
            # Try to break at newline
            idx = remaining.rfind("\n", 0, cut)
        if idx == -1:
            # Try to break at sentence end
            idx = remaining.rfind(". ", 0, cut)
            if idx != -1:
                idx += 1  # include the dot
        if idx == -1:
            # Try to break at space
            idx = remaining.rfind(" ", 0, cut)
        if idx == -1:
            idx = cut

        chunks.append(remaining[:idx].rstrip())
        remaining = remaining[idx:].lstrip()

    return chunks


async def fetch_message(message_id: str) -> dict:
    """Fetch message details (from, text) via conversations endpoint."""
    url = "https://graph.facebook.com/v25.0/me/conversations"
    params = {
        "platform": "instagram",
        "fields": "messages.limit(1){id,from,to,message,attachments,created_time}",
        "access_token": settings.FACEBOOK_PAGE_ACCESS_TOKEN,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            logger.info("[INSTAGRAM] Conversations response: %s", data)
            if resp.status_code == 200:
                for conv in data.get("data", []):
                    for msg in conv.get("messages", {}).get("data", []):
                        if msg.get("id") == message_id:
                            # If no text, try to extract from attachments
                            if not msg.get("message"):
                                msg["message"] = _extract_text_from_attachments(msg)
                            return msg
    except httpx.HTTPError as e:
        logger.error("[INSTAGRAM] HTTP error fetching conversations: %s", e)
    return {}


def _extract_text_from_attachments(msg: dict) -> str | None:
    """Extract text from message attachments (e.g. phone number contact cards)."""
    attachments = msg.get("attachments", {}).get("data", [])
    for att in attachments:
        # Contact card with phone number
        if att.get("type") == "contact":
            payload = att.get("payload", {})
            phone = payload.get("phone_number") or payload.get("contact", {}).get("phone")
            if phone:
                return phone
        # Fallback: any attachment with a name or URL as text
        name = att.get("name") or att.get("title")
        if name:
            return name
    logger.warning("[INSTAGRAM] No text extracted from attachments: %s", attachments)
    return None


async def send_message(recipient_id: str, text: str) -> bool:
    """Send a text message to an Instagram user via Graph API."""
    chunks = _split_message(text)
    headers = {
        "Authorization": f"Bearer {settings.INSTAGRAM_PAGE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        for i, chunk in enumerate(chunks):
            payload = {
                "recipient": {"id": recipient_id},
                "message": {"text": chunk},
            }
            try:
                resp = await client.post(GRAPH_API_URL, json=payload, headers=headers)
                if resp.status_code != 200:
                    logger.error("[INSTAGRAM] Failed to send message: %s", resp.text)
                    return False
            except httpx.HTTPError as e:
                logger.error("[INSTAGRAM] HTTP error sending message: %s", e)
                return False

            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)

    return True
