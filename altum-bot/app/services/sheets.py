import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Fecha",
    "Nombre",
    "Empresa",
    "Ubicacion",
    "Sector",
    "Telefono",
    "Necesidad principal",
    "Presencia digital",
    "Identidad de marca",
    "Objetivo principal",
    "Presupuesto aprox",
    "Instagram User ID",
    "Estado",
]

# Column index (1-based) of Instagram User ID — used to find existing rows
IG_USER_ID_COL = 12


def _get_client() -> gspread.Client:
    sa_info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_sheet(spreadsheet: gspread.Spreadsheet, sheet_name: str) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS)
        logger.info("[SHEETS] Created new sheet: %s", sheet_name)
        return ws


def _build_row(profile: dict, is_complete: bool) -> list:
    return [
        datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        profile.get("nombre") or "",
        profile.get("empresa") or "",
        profile.get("ubicacion") or "",
        profile.get("sector") or "",
        profile.get("telefono") or "",
        profile.get("necesidad_principal") or "",
        profile.get("presencia_digital") or "",
        profile.get("tiene_identidad_marca") or "",
        profile.get("objetivo_principal") or "",
        profile.get("presupuesto_aprox") or "",
        profile.get("instagram_user_id") or "",
        "completo" if is_complete else "en progreso",
    ]


async def upsert_prospect(profile: dict, is_complete: bool = False) -> bool:
    """
    Upsert a row in the current month's sheet keyed by instagram_user_id.
    Creates the row on first call, updates it on subsequent calls.
    """
    import asyncio

    instagram_user_id = profile.get("instagram_user_id", "")

    try:
        def _sync_upsert():
            client = _get_client()
            spreadsheet = client.open_by_key(settings.GOOGLE_SPREADSHEET_ID)
            sheet_name = datetime.utcnow().strftime("%Y-%m")
            ws = _get_or_create_sheet(spreadsheet, sheet_name)

            row = _build_row(profile, is_complete)

            try:
                cell = ws.find(instagram_user_id, in_column=IG_USER_ID_COL)
                ws.update(f"A{cell.row}:{chr(64 + len(HEADERS))}{cell.row}", [row])
                logger.info("[SHEETS] Updated row=%d user=%s complete=%s", cell.row, instagram_user_id, is_complete)
            except gspread.exceptions.CellNotFound:
                ws.append_row(row)
                logger.info("[SHEETS] Inserted new row user=%s", instagram_user_id)

        await asyncio.to_thread(_sync_upsert)
        return True
    except Exception:
        logger.exception("[SHEETS] Failed to upsert prospect user=%s", instagram_user_id)
        return False
