import asyncio
import json
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.conversation import Conversation, Message, ProspectProfile
from app.services import instagram, llm, sheets, telegram
from app.core.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

PARTIAL_TOKEN = "[PERFIL_PARCIAL]"
COMPLETE_TOKEN = "[ONBOARDING_COMPLETE]"
JSON_PATTERN = re.compile(r'\{.*?\}', re.DOTALL)

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
    if na in nb or nb in na:
        return True
    return False


async def process_message(instagram_user_id: str, text: str, message_id: str | None = None) -> None:
    """Main agent flow: receive a user message, generate a response, handle onboarding progression."""
    # Handle reset command
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

    async with _get_user_lock(instagram_user_id):
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

            # 2b. Text-similarity dedup — skip if last user message is similar and recent
            recent_cutoff = datetime.utcnow() - timedelta(seconds=10)
            last_user_msg = await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.role == "user",
                    Message.created_at >= recent_cutoff,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            last_user_msg = last_user_msg.scalar_one_or_none()
            if last_user_msg and _texts_are_similar(last_user_msg.content, text):
                logger.info("[AGENT] Similar recent message deduped: %s", text[:60])
                return

            # 3. Load message history
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at)
            )
            history = result.scalars().all()

            # 4. Save user message
            session.add(Message(
                conversation_id=conversation.id,
                role="user",
                content=text,
                instagram_message_id=message_id,
            ))
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

            # 7. Parse token — detect partial update or completion
            is_complete = COMPLETE_TOKEN in response
            token = COMPLETE_TOKEN if is_complete else PARTIAL_TOKEN

            visible_text = response
            profile_data = None

            if token in response:
                visible_text = response.split(token)[0].rstrip()
                match = JSON_PATTERN.search(response.split(token)[1])
                if match:
                    try:
                        profile_data = json.loads(match.group())
                    except json.JSONDecodeError:
                        logger.error("[AGENT] Failed to parse profile JSON")

            # 8. Save assistant message (full response with token, for history)
            session.add(Message(
                conversation_id=conversation.id,
                role="assistant",
                content=response,
            ))
            conversation.updated_at = datetime.utcnow()

            # 9. Send visible response to Instagram
            send_ok = await instagram.send_message(instagram_user_id, visible_text)

            # 10. Upsert to Sheets in background on every turn (fire-and-forget)
            if profile_data:
                profile_data["instagram_user_id"] = instagram_user_id
                asyncio.create_task(sheets.upsert_prospect(profile_data, is_complete=is_complete))

            # 11. On completion: update DB profile + send Telegram handoff
            if send_ok and is_complete and profile_data and conversation.state == "active":
                existing_profile = await session.execute(
                    select(ProspectProfile).where(ProspectProfile.conversation_id == conversation.id)
                )
                profile_row = existing_profile.scalar_one_or_none()
                if profile_row is None:
                    profile_row = ProspectProfile(conversation_id=conversation.id)
                    session.add(profile_row)

                profile_row.nombre = profile_data.get("nombre")
                profile_row.empresa = profile_data.get("empresa")
                profile_row.ubicacion = profile_data.get("ubicacion")
                profile_row.sector = profile_data.get("sector")
                profile_row.necesidad_principal = profile_data.get("necesidad_principal")
                profile_row.presencia_digital = profile_data.get("presencia_digital")
                profile_row.tiene_identidad_marca = profile_data.get("tiene_identidad_marca")
                profile_row.objetivo_principal = profile_data.get("objetivo_principal")
                profile_row.presupuesto_aprox = profile_data.get("presupuesto_aprox")
                profile_row.telefono = profile_data.get("telefono")
                profile_row.sheets_synced = True

                conversation.state = "handoff_sent"
                asyncio.create_task(telegram.send_handoff(profile_data, instagram_user_id))

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
