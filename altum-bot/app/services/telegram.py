import logging
from datetime import datetime

import httpx

from config import settings

logger = logging.getLogger(__name__)


async def send_handoff(profile: dict, instagram_user_id: str) -> bool:
    """Send a handoff notification to the ALTUM advisor on Telegram."""
    fecha = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    text = (
        "\U0001f514 *Nuevo prospecto \u2014 ALTUM*\n"
        "\n"
        f"\U0001f464 *Nombre:* {profile.get('nombre', 'N/A')}\n"
        f"\U0001f3e2 *Empresa:* {profile.get('empresa', 'N/A')}\n"
        f"\U0001f4f1 *Tel\u00e9fono:* {profile.get('telefono', 'N/A')}\n"
        f"\U0001f3ed *Sector:* {profile.get('sector', 'N/A')}\n"
        "\n"
        f"\U0001f4ac *Necesidad principal:*\n{profile.get('necesidad_principal', 'N/A')}\n"
        "\n"
        f"\U0001f310 *Presencia digital:* {profile.get('presencia_digital', 'N/A')}\n"
        f"\U0001f3a8 *Identidad de marca:* {profile.get('tiene_identidad_marca', 'N/A')}\n"
        f"\U0001f3af *Objetivo:* {profile.get('objetivo_principal', 'N/A')}\n"
        f"\U0001f4b0 *Presupuesto aprox:* {profile.get('presupuesto_aprox', 'N/A')}\n"
        "\n"
        f"\U0001f4f8 *Instagram User ID:* {instagram_user_id}\n"
        f"\U0001f5d3 *Fecha:* {fecha}"
    )

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("[TELEGRAM] prospect=%s chat_id=%s", profile.get("nombre"), settings.TELEGRAM_CHAT_ID)
                return True
            else:
                logger.error("[TELEGRAM] Failed: %s", resp.text)
                return False
    except httpx.HTTPError as e:
        logger.error("[TELEGRAM] HTTP error: %s", e)
        return False
