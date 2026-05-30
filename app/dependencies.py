# -*- coding: utf-8 -*-
"""
Dependency Injection 模組：提供 FastAPI Depends 使用的 provider 函式。

三個 DI 函式：
- GetSettings()  → Settings 設定物件（lru_cache 單例）
- GetModels()    → 已載入的 YOLO / EasyOCR 模型（從 app.state 取，不重新載入）
- GetDb()        → psycopg 連線（從 pool 借用，request 結束後歸還）
"""

from typing import Any, Dict, Generator

import psycopg
from fastapi import Depends, Request

from app.config import GetSettings, Settings


def get_settings() -> Settings:
    """
    取得型別化設定物件。
    使用 lru_cache 確保全程只有一個 Settings 實例。
    """
    return GetSettings()


def get_models(request: Request) -> Dict[str, Any]:
    """
    從 app.state 取得啟動時載入的模型字典。
    不在每次 request 重新載入（符合嚴格限制第 3 條）。

    Returns:
        dict 包含：
            - "yolo": YOLO model 實例
            - "ocrReaders": {"en": EasyOCR Reader, "zh": EasyOCR Reader}
    """
    return request.app.state.models


def get_db(request: Request) -> Generator[psycopg.Connection, None, None]:
    """
    從連線池借用 psycopg 連線，request 結束後正確歸還。

    Yields:
        psycopg.Connection 實例。
    """
    pool = request.app.state.db_pool
    with pool.connection() as conn:
        yield conn
