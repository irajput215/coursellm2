from pydantic_settings import BaseSettings
import secrets
import os
class Settings(BaseSettings):
    SECRET_KEY:str 
    ALGORITHM:str="HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES:int = 60 * 24 * 7
    DATABASE_URL:str = os.getenv("DATABASE_URL", "sqlite:///./coursellm.db")
    model_config = {
        'env_file': '.env',
        'extra': 'ignore'
    }

settings = Settings()

    