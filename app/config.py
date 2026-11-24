from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ENV: str = "dev"
    DB_URL: str = "sqlite:///./gleaners.db"
    PAY_MODE: str = "simulate"  # simulate | sandbox | agent_pay

    SECRET_KEY: str = "dev-secret-change-me-to-a-32byte-key!"
    SIM_SECRET: str = "dev-sim-secret-change-me-32bytes!!"
    FERNET_KEY: str = ""

    SIM_AUTH_DELAY: int = 2
    SIM_PAY_DELAY: int = 2
    SIM_REFUND_DELAY: int = 1

    LLM_ENABLED: bool = False
    OPENAI_BASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    SEED_ON_START: bool = True

    ALIPAY_APP_ID: str = ""
    ALIPAY_PRIVATE_KEY: str = ""
    ALIPAY_PUBLIC_KEY: str = ""


settings = Settings()