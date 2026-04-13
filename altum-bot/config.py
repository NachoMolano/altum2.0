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
