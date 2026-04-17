import asyncio
import json
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import SessionLocal
from app.models.conversation import Conversation, Message, ProspectProfile
from app.services import instagram, llm, sheets, telegram
from app.core.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

ONBOARDING_TOKEN = "[ONBOARDING_COMPLETE]"

# Per-user async locks to serialize processing and prevent race conditions
# when Instagram sends multiple webhook events for the same user message
# (e.g. message + message_edit when a phone number is auto-converted to a card).
_user_locks: dict[str, asyncio.Lock] = {}


def _get_user_lock(instagram_user_id: str) -> asyncio.Lock:
    lock = _user_locks.get(instagram_user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[instagram_user_id] = lock
    return lock


def _texts_are_similar(a: str, b: str) -> bool:
    """Return True if two user texts should be treated as the same message.
    Used to dedupe message + message_edit events from Instagram."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s or "").strip().lower()
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Either one fully contains the other (e.g. "Mi whatsapp es 300..." vs "300...")
    if na in nb or nb in na:
        return True
    return False


async def process_message(instagram_user_id: str, text: str, message_id: str | None = None) -> None:
    """Main agent flow: receive a user message, generate a response, handle onboarding completion."""
    # Handle reset command — close ALL conversations (active + handoff_sent) so the
    # next message starts fresh. This is essential for testing.
    if text.strip().upper() == "/RESET":
        async with SessionLocal() as session:
            from sqlalchemy import update
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.instagram_user_id == instagram_user_id,
                    Conversation.state.in_(["active", "handoff_sent"]),
                )
                .values(state="reset")
            )
            await session.commit()
        await instagram.send_message(instagram_user_id, "Conversación reiniciada. ¡Hola! ¿En qué puedo ayudarte?")
        logger.info("[AGENT] Conversation reset for user=%s", instagram_user_id)
        return

    async with SessionLocal() as session:
        # 1. Find or create conversation
        conversation = await _get_or_create_conversation(session, instagram_user_id)

        # 2. Check idempotency — skip if message_id already processed
        if message_id:
            existing = await session.execute(
                select(Message).where(Message.instagram_message_id == message_id)
            )
            if existing.scalar_one_or_none():
                logger.info("[AGENT] Duplicate message_id=%s, skipping", message_id)
                return

        # 3. Load message history
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
        history = result.scalars().all()

        # 4. Save user message
        user_msg = Message(
            conversation_id=conversation.id,
            role="user",
            content=text,
            instagram_message_id=message_id,
        )
        session.add(user_msg)
        await session.flush()

        # 5. Build messages for LLM
        llm_messages = [{"role": m.role, "content": m.content} for m in history]
        llm_messages.append({"role": "user", "content": text})

        # 6. Call LLM — inject conversation state so it knows if onboarding is done
        system = SYSTEM_PROMPT
        if conversation.state == "handoff_sent":
            system += "\n\n<conversation_state>HANDOFF_COMPLETE: El onboarding ya fue completado y el perfil del prospecto fue registrado. NO reinicies el flujo de preguntas. Solo responde dudas puntuales y recuerda al usuario que un asesor lo contactará pronto.</conversation_state>"

        response = await llm.chat_completion(
            messages=llm_messages,
            system=system,
        )

        # 7. Detect onboarding completion
        visible_text = response
        profile_data = None

        if ONBOARDING_TOKEN in response:
            visible_text = response.split(ONBOARDING_TOKEN)[0].rstrip()
            match = re.search(r'\[ONBOARDING_COMPLETE\]\s*(\{.*\})', response, re.DOTALL)
            if match:
                try:
                    profile_data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    logger.error("[AGENT] Failed to parse onboarding JSON")

        # 8. Save assistant message
        assistant_msg = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=response,
        )
        session.add(assistant_msg)

        # 9. Update conversation timestamp
        conversation.updated_at = datetime.utcnow()

        # 10. Send visible response to Instagram
        send_ok = await instagram.send_message(instagram_user_id, visible_text)

        # 11. Handle onboarding completion — only if message was delivered
        if send_ok and profile_data and conversation.state == "active":
            profile_data["instagram_user_id"] = instagram_user_id

            # Save profile to DB
            profile = ProspectProfile(
                conversation_id=conversation.id,
                nombre=profile_data.get("nombre"),
                empresa=profile_data.get("empresa"),
                ubicacion=profile_data.get("ubicacion"),
                sector=profile_data.get("sector"),
                necesidad_principal=profile_data.get("necesidad_principal"),
                presencia_digital=profile_data.get("presencia_digital"),
                tiene_identidad_marca=profile_data.get("tiene_identidad_marca"),
                objetivo_principal=profile_data.get("objetivo_principal"),
                presupuesto_aprox=profile_data.get("presupuesto_aprox"),
                telefono=profile_data.get("telefono"),
            )
            session.add(profile)

            # Write to Google Sheets
            sheets_ok = await sheets.append_prospect(profile_data)
            if sheets_ok:
                profile.sheets_synced = True

            # Send Telegram handoff
            await telegram.send_handoff(profile_data, instagram_user_id)

            # Mark conversation as handoff_sent
            conversation.state = "handoff_sent"

        await session.commit()


async def _get_or_create_conversation(session, instagram_user_id: str) -> Conversation:
    """Find an active or handoff_sent conversation for this user, or create a new one."""
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.instagram_user_id == instagram_user_id,
            Conversation.state.in_(["active", "handoff_sent"]),
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        conversation = Conversation(instagram_user_id=instagram_user_id)
        session.add(conversation)
        await session.flush()

    return conversation
