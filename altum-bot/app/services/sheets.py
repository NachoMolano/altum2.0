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
    "Sector",
    "Telefono",
    "Necesidad principal",
    "Presencia digital",
    "Identidad de marca",
    "Objetivo principal",
    "Presupuesto aprox",
    "Instagram User ID",
]


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


async def append_prospect(profile: dict) -> bool:
    """Write a row to the current month's sheet. Returns True on success."""
    import asyncio

    try:
        def _sync_append():
            client = _get_client()
            spreadsheet = client.open_by_key(settings.GOOGLE_SPREADSHEET_ID)
            sheet_name = datetime.utcnow().strftime("%Y-%m")
            ws = _get_or_create_sheet(spreadsheet, sheet_name)

            row = [
                datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                profile.get("nombre", ""),
                profile.get("empresa", ""),
                profile.get("sector", ""),
                profile.get("telefono", ""),
                profile.get("necesidad_principal", ""),
                profile.get("presencia_digital", ""),
                profile.get("tiene_identidad_marca", ""),
                profile.get("objetivo_principal", ""),
                profile.get("presupuesto_aprox", ""),
                profile.get("instagram_user_id", ""),
            ]
            ws.append_row(row)
            logger.info("[SHEETS] prospect=%s sheet=%s", profile.get("nombre"), sheet_name)

        await asyncio.to_thread(_sync_append)
        return True
    except Exception:
        logger.exception("[SHEETS] Failed to write prospect")
        return False
