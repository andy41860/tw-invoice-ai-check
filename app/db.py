# -*- coding: utf-8 -*-
"""
資料庫模組：PostgreSQL 連線池管理與對獎號碼查詢。

使用 psycopg v3 + psycopg_pool.ConnectionPool。
TEXT[] 欄位（first_prizes、additional_sixth_prizes）由 psycopg 自動轉為 Python list[str]，
保留前導 0（不轉 int）。
"""

from typing import Any, Dict, Optional

import psycopg
from psycopg_pool import ConnectionPool

from app.config import Settings


def CreatePool(settings: Settings) -> ConnectionPool:
    """
    建立 PostgreSQL 連線池，在服務啟動時（lifespan）呼叫一次。

    Args:
        settings: 型別化設定物件，含 PostgresDsn 屬性。

    Returns:
        已開啟的 ConnectionPool 實例。
    """
    print(f"[DB] 建立連線池：{settings.PostgresHost}:{settings.PostgresPort}/{settings.PostgresDb}")
    # min_size=1 確保啟動時立刻測試連線；max_size=10 避免過多連線
    pool = ConnectionPool(
        conninfo=settings.PostgresDsn,
        min_size=1,
        max_size=10,
        open=True,  # 立即開啟，等待連線可用
    )
    return pool


def GetAwardByPeriod(
    conn: psycopg.Connection,
    rocYear: int,
    monthStart: int,
    monthEnd: int,
) -> Optional[Dict[str, Any]]:
    """
    依民國年與期別月份查詢 invoice_awards 表，取得該期對獎號碼。

    查不到不報錯，回傳 None（讓 prize.py 處理「查無期別」的對獎結果）。

    Args:
        conn: psycopg 連線實例（由 DI 注入）。
        rocYear: 民國年，例如 115。
        monthStart: 期別起始月份，例如 1（1-2月期）。
        monthEnd: 期別結束月份，例如 2。

    Returns:
        dict 包含 invoice_awards 表所有欄位，或 None（查無資料）。
    """
    sql = """
        SELECT
            id,
            roc_year,
            period_month_start,
            period_month_end,
            period_label,
            special_prize,
            grand_prize,
            first_prizes,
            additional_sixth_prizes,
            claim_start_date,
            claim_end_date,
            source
        FROM invoice_awards
        WHERE
            roc_year = %s
            AND period_month_start = %s
            AND period_month_end = %s
        LIMIT 1
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (rocYear, monthStart, monthEnd))
        row = cursor.fetchone()

    if row is None:
        return None

    # 將 cursor.description 對應欄位名稱，組成 dict 回傳
    colNames = [
        "id", "roc_year", "period_month_start", "period_month_end",
        "period_label", "special_prize", "grand_prize",
        "first_prizes", "additional_sixth_prizes",
        "claim_start_date", "claim_end_date", "source",
    ]
    return dict(zip(colNames, row))
