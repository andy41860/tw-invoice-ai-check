# -*- coding: utf-8 -*-
"""
資料庫模組：PostgreSQL 連線池管理、對獎號碼查詢與推論結果寫入。

使用 psycopg v3 + psycopg_pool.ConnectionPool。
TEXT[] 欄位（first_prizes、additional_sixth_prizes）由 psycopg 自動轉為 Python list[str]，
保留前導 0（不轉 int）。
"""

from datetime import date, time
from typing import Any, Dict, List, Optional, Tuple

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


def InsertInferenceResult(
    conn: psycopg.Connection,
    requestId: str,
    imageName: str,
    modelVersion: Optional[str],
    invoiceNumber: Optional[str],
    invoiceDate: Optional[date],
    invoiceTime: Optional[time],
    totalAmount: Optional[int],
    merchantName: Optional[str],
    awardPeriod: Optional[str],
    isWinning: bool,
    prizeType: Optional[str],
    prizeAmount: int,
    matchedNumber: Optional[str],
) -> int:
    """
    將一筆推論結果寫入 invoice_inference_results，回傳新增記錄的 id。

    Args:
        conn: psycopg 連線實例。
        requestId: 本次推論的唯一 UUID 字串。
        imageName: 原始圖片檔名。
        modelVersion: YOLO 模型版本。
        invoiceNumber: OCR 辨識的發票號碼。
        invoiceDate: 發票日期（西元）。
        invoiceTime: 發票時間。
        totalAmount: 發票總金額。
        merchantName: 商家名稱。
        awardPeriod: 對獎期別，例如 "115年01-02月"。
        isWinning: 是否中獎。
        prizeType: 獎項類型（英文 key），未中獎為 None。
        prizeAmount: 中獎金額，未中獎為 0。
        matchedNumber: 命中的中獎號碼，未中獎為 None。

    Returns:
        新增記錄的 id（INTEGER）。
    """
    sql = """
        INSERT INTO invoice_inference_results (
            request_id,
            image_name,
            model_version,
            invoice_number,
            invoice_date,
            invoice_time,
            total_amount,
            merchant_name,
            award_period,
            is_winning,
            prize_type,
            prize_amount,
            matched_number
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            requestId,
            imageName,
            modelVersion,
            invoiceNumber,
            invoiceDate,
            invoiceTime,
            totalAmount,
            merchantName,
            awardPeriod,
            isWinning,
            prizeType,
            prizeAmount,
            matchedNumber,
        ))
        row = cursor.fetchone()
    conn.commit()
    return row[0]


def InsertInferenceConf(
    conn: psycopg.Connection,
    requestId: str,
    inferenceResultId: int,
    imageName: str,
    confidenceFields: List[Tuple[str, Optional[float]]],
) -> None:
    """
    將 YOLO 各欄位 confidence 批次寫入 invoice_inference_conf。
    每個 field 寫入一筆，共 5 筆（invoice_number, invoice_date, invoice_time, total_amount, merchant_name）。

    Args:
        conn: psycopg 連線實例。
        requestId: 本次推論的唯一 UUID 字串，與 invoice_inference_results 相同。
        inferenceResultId: 對應的 invoice_inference_results.id。
        imageName: 原始圖片檔名。
        confidenceFields: List of (field_name, confidence) tuples，confidence 可為 None。
    """
    sql = """
        INSERT INTO invoice_inference_conf (
            request_id,
            inference_result_id,
            image_name,
            field_name,
            confidence
        ) VALUES (
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (inference_result_id, field_name) DO NOTHING
    """
    with conn.cursor() as cursor:
        for fieldName, confidence in confidenceFields:
            cursor.execute(sql, (
                requestId,
                inferenceResultId,
                imageName,
                fieldName,
                confidence,
            ))
    conn.commit()
