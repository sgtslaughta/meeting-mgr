from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:test@localhost:55432/meeting_mgr_test"
    redis_url: str = "redis://localhost:56379/0"
    s3_endpoint: str = "http://localhost:59000"
    s3_access_key: str = "test"
    s3_secret_key: str = "testtest"
    s3_bucket: str = "recordings"
    asr_base_url: str = "http://localhost:58080/v1"
    asr_api_key: str = "unused"
    asr_model: str = "whisper-1"
    llm_base_url: str = "http://localhost:58080/v1"
    llm_api_key: str = "unused"
    llm_model: str = "local-model"
    diarizer_url: str = "http://localhost:58081"
    # Signs the session cookie. This default is a deliberately unsafe
    # placeholder — production deployments MUST override it (see admin guide).
    session_secret: str = "INSECURE-DEV-SESSION-SECRET-DO-NOT-USE-IN-PRODUCTION"


@lru_cache
def get_settings() -> Settings:
    return Settings()
