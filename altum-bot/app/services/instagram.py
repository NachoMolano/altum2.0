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


async def _fetch_message_direct(message_id: str) -> dict:
    """Fetch a single message directly by ID (more reliable than conversations)."""
    url = f"https://graph.facebook.com/v25.0/{message_id}"
    params = {
        "fields": "id,from,to,message,attachments{type,payload,url,title,name,mime_type,file_url},created_time",
        "access_token": settings.FACEBOOK_PAGE_ACCESS_TOKEN,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            logger.info("[INSTAGRAM] Direct message fetch response: %s", data)
            if resp.status_code == 200 and data.get("id"):
                return data
    except httpx.HTTPError as e:
        logger.error("[INSTAGRAM] HTTP error fetching message direct: %s", e)
    return {}


async def _fetch_via_conversations(message_id: str) -> dict:
    """Fetch message via conversations endpoint (fallback)."""
    url = "https://graph.facebook.com/v25.0/me/conversations"
    params = {
        "platform": "instagram",
        "fields": "messages.limit(5){id,from,to,message,attachments{type,payload,url,title,name,mime_type,file_url},created_time}",
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
                            return msg
    except httpx.HTTPError as e:
        logger.error("[INSTAGRAM] HTTP error fetching conversations: %s", e)
    return {}


async def fetch_message(message_id: str, max_retries: int = 3) -> dict:
    """Fetch message details with retry. Tries direct fetch first, then conversations."""
    for attempt in range(max_retries):
        if attempt > 0:
            # Wait before retry — Meta may need time to index the message
            await asyncio.sleep(1.5 * attempt)
            logger.info("[INSTAGRAM] Retry attempt %d for mid=%s", attempt + 1, message_id)

        # Try direct fetch first (more reliable)
        msg = await _fetch_message_direct(message_id)
        if not msg:
            msg = await _fetch_via_conversations(message_id)

        if not msg:
            continue

        # If message is empty, try extracting from attachments
        if not msg.get("message"):
            extracted = _extract_text_from_attachments(msg)
            if extracted:
                msg["message"] = extracted

        # Last resort: regex phone extraction from any string field
        if not msg.get("message"):
            phone = _extract_phone_from_any(msg)
            if phone:
                logger.info("[INSTAGRAM] Extracted phone via regex: %s", phone)
                msg["message"] = phone

        if msg.get("message"):
            return msg

    logger.warning("[INSTAGRAM] fetch_message exhausted retries for mid=%s", message_id)
    return msg if msg else {}


def _extract_phone_from_any(obj) -> str | None:
    """Recursively search a dict/list structure for a phone-number-shaped string."""
    import re
    phone_re = re.compile(r"\+?\d[\d\s\-().]{6,18}\d")

    def walk(node):
        if isinstance(node, str):
            m = phone_re.search(node)
            if m:
                # Keep only digits and leading +
                raw = m.group(0)
                cleaned = re.sub(r"[^\d+]", "", raw)
                if len(re.sub(r"\D", "", cleaned)) >= 7:
                    return cleaned
        elif isinstance(node, dict):
            for v in node.values():
                found = walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    return walk(obj)


def _extract_text_from_attachments(msg: dict) -> str | None:
    """Extract text from message attachments (e.g. phone number contact cards)."""
    attachments = msg.get("attachments", {}).get("data", [])
    logger.info("[INSTAGRAM] Extracting from attachments: %s", attachments)
    for att in attachments:
        att_type = att.get("type", "")
        att_payload = att.get("payload", {})

        # Explicit contact card shared from contacts app
        if att_type == "contact":
            phone = (
                att_payload.get("phone_number")
                or att_payload.get("contact", {}).get("phone")
            )
            if phone:
                return phone

        # Instagram converts typed phone numbers into fallback attachments with tel: URL
        if att_type == "fallback":
            url = att_payload.get("url", "")
            if url.lower().startswith("tel:"):
                phone = url[4:].replace("%2B", "+").replace("%20", "").strip()
                if phone:
                    return phone
            title = att_payload.get("title") or att.get("title")
            if title:
                return title

        # Generic fallback: name or title field
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
