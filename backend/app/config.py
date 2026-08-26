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
    # JWT_SECRET_KEY 生成方法：在终端运行 openssl rand -hex 32
    # 示例输出：a3f8b2c1d4e5f6789abc0def12345678abcdef01234567890abcdef01234567
    # 将生成的字符串写入 .env：JWT_SECRET_KEY=生成的字符串
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7天

    # 同花顺官方金融数据API（https://fuyao.aicubes.cn）的 API Key。
    # 配置后 daily_update 的K线走"全市场日K dump 一次下载"，替代逐股拉取几百次请求。
    # 空 = 未启用，完全退回腾讯/新浪逐股接口，功能不受影响。
    FUYAO_API_KEY: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
