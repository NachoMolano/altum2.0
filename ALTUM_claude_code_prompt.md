# ALTUM Onboarding Bot — Prompt para Claude Code

## Objetivo

Construye un sistema de chatbot de onboarding para ALTUM, una agencia de marketing. El bot opera en Instagram vía Graph API, conduce una conversación estructurada para recopilar el perfil del prospecto, guarda los resultados en Google Sheets y hace handoff a un asesor humano vía Telegram.

---

## Stack tecnológico

- **Backend**: FastAPI (Python 3.11+)
- **Base de datos**: PostgreSQL (estado de conversación e historial)
- **LLM**: Configurable por variable de entorno — Claude Sonnet 4 (`claude-sonnet-4-20250514`) o Gemini Flash 2.5 (`gemini-2.5-flash-preview-05-20`)
- **Canal de entrada**: Instagram Graph API (webhook)
- **Almacenamiento de resultados**: Google Sheets API (service account existente)
- **Notificación de handoff**: Telegram Bot API
- **Deploy**: Railway

---

## Estructura del proyecto

Crea el proyecto con esta estructura exacta:

```
altum-bot/
├── main.py                    # Entry point FastAPI
├── config.py                  # Settings con pydantic-settings
├── requirements.txt
├── railway.json               # Configuración deploy Railway
├── Procfile
├── .env.example
├── app/
│   ├── __init__.py
│   ├── routes/
│   │   ├── __init__.py
│   │   └── webhook.py         # POST /webhook/instagram, GET verificación
│   ├── services/
│   │   ├── __init__.py
│   │   ├── instagram.py       # Enviar mensajes vía Graph API
│   │   ├── llm.py             # Abstracción LLM (Claude o Gemini)
│   │   ├── sheets.py          # Escritura en Google Sheets
│   │   └── telegram.py        # Envío de notificación de handoff
│   ├── models/
│   │   ├── __init__.py
│   │   └── conversation.py    # SQLAlchemy models
│   ├── db/
│   │   ├── __init__.py
│   │   └── session.py         # Engine, SessionLocal, Base
│   └── core/
│       ├── __init__.py
│       ├── agent.py           # Lógica principal del agente
│       └── prompts.py         # System prompt de ALTUM
├── alembic/
│   └── versions/
├── alembic.ini
```

---

## Variables de entorno

Crea `.env.example` con todas las variables requeridas. El sistema debe leer estas variables en `config.py` usando `pydantic-settings`:

```env
# LLM — escribe "claude" o "gemini"
LLM_PROVIDER=claude

# Anthropic
ANTHROPIC_API_KEY=

# Google Gemini
GOOGLE_API_KEY=

# Instagram / Meta
INSTAGRAM_APP_ID=
INSTAGRAM_APP_SECRET=
INSTAGRAM_VERIFY_TOKEN=        # Token arbitrario para verificar el webhook
INSTAGRAM_PAGE_ACCESS_TOKEN=   # Token de acceso de página con permisos instagram_manage_messages

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/altum_bot

# Google Sheets
GOOGLE_SERVICE_ACCOUNT_JSON=   # JSON completo del service account como string (una línea)
GOOGLE_SPREADSHEET_ID=         # ID del spreadsheet de Google Sheets

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=              # Chat ID del asesor ALTUM que recibe el handoff
```

En `config.py`:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    LLM_PROVIDER: str = "claude"
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    INSTAGRAM_APP_SECRET: str
    INSTAGRAM_VERIFY_TOKEN: str
    INSTAGRAM_PAGE_ACCESS_TOKEN: str
    DATABASE_URL: str
    GOOGLE_SERVICE_ACCOUNT_JSON: str
    GOOGLE_SPREADSHEET_ID: str
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## Base de datos — modelos PostgreSQL

En `app/models/conversation.py`, crea con SQLAlchemy async estas dos tablas:

### Tabla `conversations`

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | UUID PK | Identificador único |
| `instagram_user_id` | VARCHAR | PSID del usuario en Instagram |
| `state` | VARCHAR | Estado actual: `active`, `completed`, `handoff_sent` |
| `created_at` | TIMESTAMP | Fecha de inicio |
| `updated_at` | TIMESTAMP | Última actividad |

### Tabla `messages`

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | UUID PK | Identificador único |
| `conversation_id` | UUID FK → conversations | Conversación a la que pertenece |
| `role` | VARCHAR | `user` o `assistant` |
| `content` | TEXT | Contenido del mensaje |
| `created_at` | TIMESTAMP | Timestamp del mensaje |

### Tabla `prospect_profiles`

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | UUID PK | Identificador único |
| `conversation_id` | UUID FK → conversations | Conversación asociada |
| `nombre` | VARCHAR | Nombre del prospecto |
| `empresa` | VARCHAR | Nombre de la empresa |
| `sector` | VARCHAR | Industria o sector |
| `necesidad_principal` | TEXT | Problema o necesidad declarada |
| `presencia_digital` | VARCHAR | Redes activas, web, estado actual |
| `tiene_identidad_marca` | VARCHAR | Sí/No/Parcial + descripción |
| `objetivo_principal` | TEXT | Lo que busca lograr |
| `presupuesto_aprox` | VARCHAR | Rango o descripción del presupuesto |
| `telefono` | VARCHAR | Número de teléfono recopilado en la conversación |
| `sheets_synced` | BOOLEAN | Si ya fue escrito en Google Sheets |
| `created_at` | TIMESTAMP | Fecha de creación |

Crea las migraciones con Alembic. Incluye `env.py` configurado para async con `asyncpg`.

---

## Webhook de Instagram

En `app/routes/webhook.py`:

### GET `/webhook/instagram`
Verificación del webhook de Meta. Valida `hub.mode == "subscribe"`, `hub.verify_token` contra `settings.INSTAGRAM_VERIFY_TOKEN`, y retorna `hub.challenge` como texto plano.

### POST `/webhook/instagram`
1. Valida la firma `X-Hub-Signature-256` con HMAC-SHA256 usando `INSTAGRAM_APP_SECRET`. Retorna 403 si falla.
2. Parsea el payload. El campo relevante es `entry[].messaging[]` con `sender.id` y `message.text`.
3. Ignora mensajes donde `sender.id == recipient.id` (eco propio).
4. Llama a `agent.process_message(instagram_user_id, message_text)` de forma async.
5. Retorna `200 OK` inmediatamente (Meta requiere respuesta en < 200ms — usa `BackgroundTasks` de FastAPI para procesar el mensaje en background).

---

## Abstracción LLM

En `app/services/llm.py`, crea una interfaz unificada que soporte ambos proveedores:

```python
async def chat_completion(
    messages: list[dict],   # [{"role": "user/assistant", "content": "..."}]
    system: str,            # System prompt
    max_tokens: int = 800
) -> str:
    """
    Retorna el texto de respuesta del modelo.
    Lee settings.LLM_PROVIDER para decidir qué cliente usar.
    """
```

Para **Claude** usa el SDK oficial `anthropic` con el cliente async. Modelo: `claude-sonnet-4-20250514`.

Para **Gemini** usa `google-generativeai`. Modelo: `gemini-2.5-flash-preview-05-20`. Convierte el formato de mensajes de OpenAI-style a Gemini-style (roles `user`/`model`, system prompt como parte del primer mensaje de usuario si el SDK no lo soporta directamente).

Maneja rate limits y errores de API con reintentos exponenciales (máximo 3 intentos, backoff de 1s, 2s, 4s).

---

## Lógica del agente

En `app/core/agent.py`, implementa `process_message(instagram_user_id: str, text: str)`:

### Flujo principal

```
1. Buscar o crear conversación en PostgreSQL para instagram_user_id
2. Cargar historial completo de mensajes de esa conversación
3. Agregar el nuevo mensaje del usuario al historial
4. Llamar a llm.chat_completion con system prompt + historial
5. Guardar respuesta del assistant en la tabla messages
6. Enviar respuesta vía instagram.send_message()
7. Detectar si el onboarding está completo
8. Si está completo y no se ha hecho handoff:
   a. Extraer perfil del historial
   b. Guardar en prospect_profiles
   c. Escribir en Google Sheets
   d. Enviar notificación a Telegram
   e. Marcar conversación como handoff_sent
```

### Detección de onboarding completo

El LLM incluirá en su respuesta un marcador especial cuando haya recopilado todos los datos requeridos. Instrúyelo en el system prompt para que incluya al final de su mensaje el token `[ONBOARDING_COMPLETE]` seguido de un bloque JSON con el perfil:

```
[ONBOARDING_COMPLETE]
{"nombre":"...","empresa":"...","sector":"...","necesidad_principal":"...","presencia_digital":"...","tiene_identidad_marca":"...","objetivo_principal":"...","presupuesto_aprox":"...","telefono":"..."}
```

En `agent.py`, detecta este token con un simple `"[ONBOARDING_COMPLETE]" in response`. Extrae el JSON con `re.search(r'\[ONBOARDING_COMPLETE\]\s*(\{.*\})', response, re.DOTALL)`. El mensaje visible para el usuario es solo la parte antes del token.

---

## Google Sheets

En `app/services/sheets.py`:

Autentica usando el JSON del service account desde `settings.GOOGLE_SERVICE_ACCOUNT_JSON` (parsear con `json.loads`). Usa `gspread` con `gspread.auth.service_account_from_dict()`.

### Lógica de hoja por mes

- Nombre de hoja: `YYYY-MM` (ej: `2026-04`)
- Si la hoja del mes actual no existe, créala y agrega una fila de encabezados
- Encabezados: `Fecha | Nombre | Empresa | Sector | Teléfono | Necesidad principal | Presencia digital | Identidad de marca | Objetivo principal | Presupuesto aprox | Instagram User ID`

### Función principal

```python
async def append_prospect(profile: dict) -> bool:
    """
    Escribe una fila en la hoja del mes actual.
    Retorna True si fue exitoso.
    """
```

Maneja el error `gspread.exceptions.WorksheetNotFound` creando la hoja automáticamente.

---

## Telegram — handoff

En `app/services/telegram.py`:

Usa `httpx` para llamar a la Bot API de Telegram (no hace falta SDK).

### Formato del mensaje de handoff

```
🔔 *Nuevo prospecto — ALTUM*

👤 *Nombre:* {nombre}
🏢 *Empresa:* {empresa}
📱 *Teléfono:* {telefono}
🏭 *Sector:* {sector}

💬 *Necesidad principal:*
{necesidad_principal}

🌐 *Presencia digital:* {presencia_digital}
🎨 *Identidad de marca:* {tiene_identidad_marca}
🎯 *Objetivo:* {objetivo_principal}
💰 *Presupuesto aprox:* {presupuesto_aprox}

📸 *Instagram User ID:* {instagram_user_id}
🗓 *Fecha:* {fecha}
```

Enviar como `parse_mode=Markdown` al `TELEGRAM_CHAT_ID`.

---

## System prompt del agente

En `app/core/prompts.py`, define el system prompt en formato XML estructurado:

```xml
<agent_identity>
Eres el asistente de onboarding de ALTUM. Tu misión es conocer la empresa
del prospecto de forma conversacional, recopilar su perfil completo y
transferirlo a un asesor humano de ALTUM.

Tono: profesional pero cercano. Lenguaje claro, sin jerga técnica.
Canal: Instagram DM — mensajes cortos. Nunca más de una pregunta por mensaje.
Nunca envíes listas largas de golpe. Divide la información en pasos.
</agent_identity>

<company_context>
  <what_is_altum>
    ALTUM es una agencia de marketing integral fundada en 2025, con sede en
    Acacías (Meta, Colombia) y presencia en Guamal y Villavicencio. Metodología
    360°: estrategia, contenido y conversión end-to-end. Especialistas en B2C
    con visión de expansión a B2B.
  </what_is_altum>

  <plans>
    GENESIS — Branding e identidad visual desde cero. Incluye: logo, paleta,
    tipografías, narrativa de marca, manual de uso y activos gráficos iniciales.
    Dirigido a: emprendedores y marcas nuevas o que necesitan rebranding.

    ESSENTIA — Contenido y narrativa para redes sociales. Incluye: estrategia
    de contenido mensual, producción técnica (grabación y edición), narrativa
    para redes. Opcionalmente incluye identidad visual del plan Genesis.
    Dirigido a: negocios con identidad pero sin presencia digital constante.

    PLURA — Ecosistema digital completo. Incluye todo de Essentia más diseño y
    desarrollo de página web optimizada para conversión.
    Dirigido a: empresas que quieren centralizar su tráfico en un canal propio.

    ALTUM (plan) — Gestión 360° end-to-end. Incluye todo de Plura más community
    management total y creación de pauta publicitaria (Ads).
    Dirigido a: empresas consolidadas que necesitan un departamento de marketing
    externo completo.

    Todos los precios son por cotización personalizada.
  </plans>

  <methodology>
    1. Fase de inteligencia: análisis rápido de mercado y competencia.
    2. Ciclos de contenido end-to-end: ideación, producción, distribución.
    3. Auditoría mensual de crecimiento: métricas, engagement, ajuste estratégico.
  </methodology>

  <ideal_client>
    Marcas emergentes o empresas consolidadas con mentalidad de
    profesionalización. No hay restricción de industria. El factor determinante
    es la disposición a colaborar estratégicamente.
  </ideal_client>

  <social_proof>
    - 3 Esquinas (automotriz): 76.000 reproducciones en 3 meses, 605 seguidores
      orgánicos. Rebranding que posicionó la empresa como referente regional.
    - Bosques de San Francisco (inmobiliario): proyecto local con alcance nacional
      gracias a contenido aspiracional dirigido a inversionistas.
    - ByMila (fitness/pilates): incremento directo en conversiones y ventas tras
      nueva identidad visual y catálogo de servicios.
  </social_proof>

  <policies>
    - Entrega de material: máx. 7 días tras sesión de grabación.
    - Revisiones: 2 días hábiles tras entrega.
    - Pago: 100% anticipado o 50% inicio + 50% día 15 del mes.
    - Métodos: efectivo y factura electrónica.
    - No trabajan bajo modelos de comisión o porcentaje de ventas.
  </policies>

  <what_altum_does_not_do>
    Cualquier servicio fuera de los 4 planes se cotiza aparte. No trabajan
    bajo modelos de comisión o riesgo compartido.
  </what_altum_does_not_do>
</company_context>

<onboarding_flow>
  Recoge estos datos en orden conversacional, una pregunta a la vez.
  Adapta las preguntas al contexto que ya conoces — no repitas información
  que el usuario ya compartió.

  PASO 1 — Saludo y presentación
  Preséntate brevemente como el asistente de ALTUM y pregunta el nombre y
  empresa del prospecto.

  PASO 2 — Actividad de la empresa
  Pregunta a qué se dedica la empresa y cuál es su propuesta de valor o
  diferencial en el mercado.

  PASO 3 — Situación actual de marketing
  Pregunta cuál es su principal dolor o necesidad en marketing hoy.

  PASO 4 — Presencia digital actual
  Pregunta si tienen redes sociales activas y/o página web, y cómo está
  ese ecosistema actualmente.

  PASO 5 — Identidad de marca
  Pregunta si tienen una identidad de marca definida (logo, colores,
  tipografías, manual de marca).

  PASO 6 — Objetivo principal
  Pregunta qué resultado concreto quieren lograr contratando a ALTUM.
  ¿Visibilidad? ¿Ventas? ¿Imagen profesional? ¿Lanzamiento de marca?

  PASO 7 — Presupuesto
  Pregunta si tienen un presupuesto mensual aproximado en mente para
  invertir en marketing. Explica que los planes de ALTUM son por
  cotización personalizada.

  PASO 8 — Número de teléfono
  Informa que para que el equipo de ALTUM pueda contactarlos, necesitas
  su número de teléfono (WhatsApp de preferencia). Pide el número.

  PASO 9 — Cierre
  Agradece, confirma que el perfil fue registrado, e informa que un asesor
  de ALTUM los contactará pronto para presentarles una propuesta personalizada.

  Cuando hayas completado los 9 pasos y tengas todos los datos, incluye al
  final de tu último mensaje el marcador especial:

  [ONBOARDING_COMPLETE]
  {"nombre":"...","empresa":"...","sector":"...","necesidad_principal":"...",
  "presencia_digital":"...","tiene_identidad_marca":"...","objetivo_principal":"...",
  "presupuesto_aprox":"...","telefono":"..."}

  El JSON debe ir en una sola línea. El mensaje visible para el usuario es
  solo el texto antes del marcador.
</onboarding_flow>

<handoff_rules>
  Si el usuario pregunta precios exactos:
  "Los planes de ALTUM son por cotización personalizada. Con el perfil que
  construimos juntos, el equipo te preparará una propuesta a la medida de
  tu empresa. ¿Continuamos?"

  Si el usuario pregunta algo fuera del alcance del onboarding:
  "Eso lo podrá resolver mejor el equipo de ALTUM cuando te contacten.
  Lo que sí puedo hacer es asegurarme de que tu perfil esté completo para
  que la conversación sea lo más útil posible."

  Si el usuario quiere hablar con una persona ahora:
  "Entendido. Voy a registrar tus datos para que un asesor de ALTUM se
  comunique contigo a la brevedad. ¿Me confirmas tu nombre y número de
  teléfono?"
</handoff_rules>
```

---

## Servicio de Instagram

En `app/services/instagram.py`:

```python
async def send_message(recipient_id: str, text: str) -> bool:
    """
    Envía un mensaje de texto al usuario de Instagram.
    Usa la Graph API: POST /v19.0/me/messages
    Headers: Authorization Bearer INSTAGRAM_PAGE_ACCESS_TOKEN
    Body: {"recipient": {"id": recipient_id}, "message": {"text": text}}
    Retorna True si status_code == 200.
    """
```

Usa `httpx.AsyncClient`. Maneja el límite de 1000 caracteres por mensaje de Instagram — si el texto es más largo, divídelo en fragmentos de máximo 900 caracteres (dividir por oraciones o párrafos, no a mitad de palabra) y envíalos en secuencia con un `await asyncio.sleep(0.3)` entre cada uno.

---

## Deploy en Railway

### `railway.json`

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/health",
    "restartPolicyType": "ON_FAILURE"
  }
}
```

### `Procfile`

```
web: alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT
```

### Endpoint de salud

En `main.py`, agrega:

```python
@app.get("/health")
async def health():
    return {"status": "ok"}
```

### PostgreSQL en Railway

Instrúyeme a crear un servicio PostgreSQL en Railway y copiar la `DATABASE_URL` de las variables de entorno al servicio del bot. La variable se llama `DATABASE_URL` en Railway y debe usar el driver `postgresql+asyncpg://`.

---

## `requirements.txt`

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
alembic==1.14.0
pydantic-settings==2.5.2
httpx==0.27.2
anthropic==0.40.0
google-generativeai==0.8.3
gspread==6.1.3
google-auth==2.35.0
python-dotenv==1.0.1
```

---

## Consideraciones de seguridad

1. **Firma del webhook**: Siempre validar `X-Hub-Signature-256` antes de procesar cualquier mensaje. Si falla, retornar 403 y loggear el intento.
2. **Variables de entorno**: Nunca hardcodear keys. El JSON del service account debe estar en una variable de entorno como string.
3. **Rate limiting**: Instagram permite ~10 mensajes por segundo por página. El agente no enviará ráfagas — hay como máximo 1 respuesta por mensaje entrante.
4. **Idempotencia**: Meta puede reenviar webhooks. Antes de procesar un mensaje, verificar si ya existe en la base de datos por `message_id` (guardar el ID del mensaje de Instagram en la tabla `messages`).

---

## Logging

Usa el módulo `logging` de Python con nivel `INFO`. Loggea:
- Cada mensaje entrante: `[WEBHOOK] user_id={id} text_preview={primeros 50 chars}`
- Cada llamada al LLM: `[LLM] provider={provider} tokens_aprox={len}`
- Cada escritura en Sheets: `[SHEETS] prospect={nombre} sheet={mes}`
- Cada handoff a Telegram: `[TELEGRAM] prospect={nombre} chat_id={id}`
- Cualquier error con stack trace completo

---

## Pasos de implementación sugeridos (en orden)

1. Crear estructura de archivos y `requirements.txt`
2. Implementar `config.py` con pydantic-settings
3. Configurar `db/session.py` y modelos SQLAlchemy
4. Crear migraciones con Alembic
5. Implementar `services/instagram.py`
6. Implementar `services/llm.py` con ambos proveedores
7. Implementar `services/sheets.py`
8. Implementar `services/telegram.py`
9. Implementar `core/prompts.py` con el system prompt completo
10. Implementar `core/agent.py` con la lógica de onboarding
11. Implementar `routes/webhook.py`
12. Ensamblar `main.py`
13. Crear `railway.json` y `Procfile`
14. Crear `.env.example`
15. Verificar que `alembic upgrade head` corre sin errores
16. Probar localmente con ngrok apuntando el webhook de Meta al endpoint local
