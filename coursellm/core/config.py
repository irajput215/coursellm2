from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://irajput@localhost:5432/ai_tutor"
    )
    GROQ_API_KEY: str

    EMAIL_IMAP_HOST: str | None = None
    EMAIL_IMAP_PORT: int = 993
    EMAIL_ADDRESS: str | None = None
    EMAIL_PASSWORD: str | None = None
    EMAIL_USE_MOCK: bool | None = None
    EMAIL_USE_LLM_EXTRACTION: bool = True

    OBSERVABILITY_ENABLED: bool = True
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    OBSERVABILITY_DEBUG_API_ENABLED: bool = True

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }

    @property
    def email_use_mock(self) -> bool:
        if self.EMAIL_USE_MOCK is not None:
            return self.EMAIL_USE_MOCK
        return not (
            self.EMAIL_IMAP_HOST
            and self.EMAIL_ADDRESS
            and self.EMAIL_PASSWORD
        )


settings = Settings()
