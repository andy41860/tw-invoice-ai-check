# -*- coding: utf-8 -*-
"""
設定模組：使用 pydantic-settings 讀取 .env，提供型別化設定物件。
所有環境變數皆在此定義，其他模組透過 DI 注入 Settings 實例。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    應用程式設定類別。
    欄位預設值對應 .env.example 內容；可由 .env 或環境變數覆寫。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ---- FastAPI / ASGI ----
    # 對外服務 host，容器內建議 0.0.0.0
    AppHost: str = "0.0.0.0"
    # 對外服務 port
    AppPort: int = 8000

    # ---- Model ----
    # 權重版本，組成路徑 model/<ModelVersion>/<ModelWeightName>
    ModelVersion: str = "v1.0"
    # 權重檔名
    ModelWeightName: str = "best.pt"
    # 推論裝置，固定 cpu
    Device: str = "cpu"
    # YOLO confidence threshold
    YoloConf: float = 0.25
    # YOLO 推論圖片尺寸
    YoloImgsz: int = 960

    # ---- PostgreSQL ----
    # docker compose 內以服務名 db 連線
    PostgresHost: str = "db"
    # 容器內部 port
    PostgresPort: int = 5432
    # 資料庫名稱
    PostgresDb: str = "invoice"
    # 資料庫帳號
    PostgresUser: str = "postgres"
    # 資料庫密碼
    PostgresPassword: str = "postgres"
    # 對 host 曝露的 DB port（host:container = <外部port>:5432）
    PostgresExternalPort: int = 5432

    @property
    def ModelWeightPath(self) -> Path:
        """組合 YOLO 權重完整路徑：model/<ModelVersion>/<ModelWeightName>"""
        return Path("model") / self.ModelVersion / self.ModelWeightName

    @property
    def PostgresDsn(self) -> str:
        """組合 psycopg v3 連線字串"""
        return (
            f"host={self.PostgresHost} "
            f"port={self.PostgresPort} "
            f"dbname={self.PostgresDb} "
            f"user={self.PostgresUser} "
            f"password={self.PostgresPassword}"
        )


@lru_cache
def GetSettings() -> Settings:
    """
    取得 Settings 單例（使用 lru_cache，全程只建立一次）。
    透過 FastAPI Depends 注入到各 route handler。
    """
    return Settings()
