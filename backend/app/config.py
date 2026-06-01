from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://chao@localhost/tradeflux"
    API_PREFIX: str = "/api"
    DEBUG: bool = True
    APP_NAME: str = "TradeFlux"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # 认证配置（可在 .env 中覆盖）
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "tradeflux2026"   # 建议在 .env 中修改
    JWT_SECRET_KEY: str = "tradeflux-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7天

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
