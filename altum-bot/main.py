import logging

from fastapi import FastAPI

from app.routes.webhook import router as webhook_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)

app = FastAPI(title="ALTUM Onboarding Bot")

app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
