# -*- coding: utf-8 -*-
"""
對獎邏輯模組：依 invoice_rules.txt 實作台灣統一發票兌獎規則。

核心規則：
- 比對發票號碼後 8 碼數字（字軌英文字母不參與對獎）。
- 依發票日期所屬期別（雙月一期）查 DB 取對獎號碼。
- 依最高獎項優先順序比對（不先判末 3 碼，避免頭獎被誤判成六獎）。
- 前導 0 必須保留，全程使用字串，不轉 int。
"""

import re
from datetime import date
from typing import Any, Dict, Optional

import psycopg

from app.db import GetAwardByPeriod
from app.schemas import AwardResult


# ---- 獎項金額常數（集中定義，不散落程式各處）----
PRIZE_AMOUNT: Dict[str, int] = {
    "special_prize": 10_000_000,       # 特別獎
    "grand_prize": 2_000_000,          # 特獎
    "first_prize": 200_000,            # 頭獎
    "second_prize": 40_000,            # 二獎
    "third_prize": 10_000,             # 三獎
    "fourth_prize": 4_000,             # 四獎
    "fifth_prize": 1_000,              # 五獎
    "sixth_prize": 200,                # 六獎
    "additional_sixth_prize": 200,     # 增開六獎
}

# ---- 獎項中文名稱常數（供 log / 內部使用，不進 response）----
PRIZE_NAME: Dict[str, str] = {
    "special_prize": "特別獎",
    "grand_prize": "特獎",
    "first_prize": "頭獎",
    "second_prize": "二獎",
    "third_prize": "三獎",
    "fourth_prize": "四獎",
    "fifth_prize": "五獎",
    "sixth_prize": "六獎",
    "additional_sixth_prize": "增開六獎",
}


def NormalizeInvoiceNumber(invoiceNumber: Optional[str]) -> Optional[str]:
    """
    正規化發票號碼：取最後 8 碼數字，保留前導 0。

    規則：
    - None 或空字串 → None（無法對獎）
    - 轉大寫，移除空白、全形空白、破折號、底線、冒號等符號
    - 取出所有數字
    - 數字長度 >= 8：取最後 8 碼
    - 數字長度 < 8：回傳 None

    Args:
        invoiceNumber: OCR 辨識的發票號碼，例如 "AB-12345678"。

    Returns:
        8 碼數字字串（保留前導 0）或 None。

    Examples:
        "AB-12345678" → "12345678"
        "AB12345678"  → "12345678"
        "07225810"    → "07225810"（前導 0 保留）
        "12345"       → None（不足 8 碼）
    """
    if not invoiceNumber:
        return None

    # 轉大寫、移除各種符號
    cleaned = invoiceNumber.upper()
    cleaned = re.sub(r"[\s　\-_:：－—–]", "", cleaned)

    # 取出所有數字字符
    digitsOnly = re.sub(r"[^\d]", "", cleaned)

    if len(digitsOnly) < 8:
        return None

    # 取最後 8 碼（保留前導 0，不轉 int）
    return digitsOnly[-8:]


def GetRocPeriod(invoiceDate: date) -> Dict[str, int]:
    """
    依發票西元日期計算對應的民國年與期別月份。

    規則：
    - roc_year = invoice_date.year - 1911
    - 雙月一期：1-2月, 3-4月, 5-6月, 7-8月, 9-10月, 11-12月

    Args:
        invoiceDate: 發票西元日期，例如 date(2026, 1, 15)。

    Returns:
        dict 包含：
            - roc_year: 民國年（int）
            - period_month_start: 期別起始月（int）
            - period_month_end: 期別結束月（int）

    Examples:
        date(2026, 1, 15) → {"roc_year": 115, "period_month_start": 1, "period_month_end": 2}
        date(2026, 4, 30) → {"roc_year": 115, "period_month_start": 3, "period_month_end": 4}
    """
    rocYear = invoiceDate.year - 1911
    month = invoiceDate.month

    # 奇數月為起始月（1, 3, 5, 7, 9, 11）
    periodMonthStart = month if month % 2 == 1 else month - 1
    periodMonthEnd = periodMonthStart + 1

    return {
        "roc_year": rocYear,
        "period_month_start": periodMonthStart,
        "period_month_end": periodMonthEnd,
    }


def BuildPeriodLabel(rocYear: int, startMonth: int, endMonth: int) -> str:
    """
    組合期別顯示字串，例如 "115年01-02月"。

    Args:
        rocYear: 民國年。
        startMonth: 期別起始月份。
        endMonth: 期別結束月份。

    Returns:
        格式化期別字串，例如 "115年01-02月"。
    """
    return f"{rocYear}年{startMonth:02d}-{endMonth:02d}月"


def CheckInvoiceAward(
    invoiceNumber: Optional[str],
    invoiceDate: Optional[date],
    conn: psycopg.Connection,
) -> AwardResult:
    """
    執行台灣統一發票對獎完整流程：
    1. 正規化發票號碼（取後 8 碼數字）
    2. 依發票日期計算民國年與期別
    3. 查 DB 取對應期別的對獎號碼
    4. 依最高獎項優先順序比對
    5. 回傳 AwardResult

    邊界條件處理：
    - invoice_number 為 None 或不足 8 碼 → 未中獎
    - invoice_date 為 None → period=None，未中獎
    - DB 查不到該期資料 → 未中獎（自組 period label）
    - first_prizes 為空陣列 → 跳過頭獎到六獎比對
    - 前導 0 全程保留

    Args:
        invoiceNumber: OCR 辨識的發票號碼（可為 None）。
        invoiceDate: 發票西元日期（可為 None）。
        conn: psycopg 連線實例。

    Returns:
        AwardResult Pydantic 模型（嚴格對齊 api_schema.txt 的 5 欄位）。
    """
    # ---- 無效輸入：invoice_date 為 None → 無法查期別 ----
    if invoiceDate is None:
        return AwardResult(
            period=None,
            is_winning=False,
            prize_type=None,
            prize_amount=0,
            matched_number=None,
        )

    # ---- 計算期別 ----
    periodInfo = GetRocPeriod(invoiceDate)
    rocYear = periodInfo["roc_year"]
    monthStart = periodInfo["period_month_start"]
    monthEnd = periodInfo["period_month_end"]
    periodLabel = BuildPeriodLabel(rocYear, monthStart, monthEnd)

    # ---- 正規化發票號碼（取後 8 碼數字）----
    invoice8Digits = NormalizeInvoiceNumber(invoiceNumber)

    # 發票號碼無法正規化 → 未中獎
    if invoice8Digits is None:
        return AwardResult(
            period=periodLabel,
            is_winning=False,
            prize_type=None,
            prize_amount=0,
            matched_number=None,
        )

    # ---- 查詢 DB 該期對獎號碼 ----
    awardRow = GetAwardByPeriod(conn, rocYear, monthStart, monthEnd)

    # DB 查不到該期 → 未中獎（不報 500）
    if awardRow is None:
        print(f"[Prize][INFO] 查無期別資料：{periodLabel}，視為未中獎。")
        return AwardResult(
            period=periodLabel,
            is_winning=False,
            prize_type=None,
            prize_amount=0,
            matched_number=None,
        )

    # 取出對獎號碼（皆為字串，保留前導 0）
    specialPrize: str = awardRow["special_prize"]
    grandPrize: str = awardRow["grand_prize"]
    firstPrizes: list = awardRow.get("first_prizes") or []
    additionalSixthPrizes: list = awardRow.get("additional_sixth_prizes") or []

    # 以 DB 的 period_label 為準（若有的話）
    dbPeriodLabel: str = awardRow.get("period_label") or periodLabel

    # ---- 依最高獎項優先順序比對 ----
    # 重要：必須從最高獎開始，不可先判末 3 碼

    # A. 特別獎：8 碼完全相等
    if invoice8Digits == specialPrize:
        prizeName = PRIZE_NAME["special_prize"]
        print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（{invoice8Digits}）")
        return AwardResult(
            period=dbPeriodLabel,
            is_winning=True,
            prize_type="special_prize",
            prize_amount=PRIZE_AMOUNT["special_prize"],
            matched_number=specialPrize,
        )

    # B. 特獎：8 碼完全相等
    if invoice8Digits == grandPrize:
        prizeName = PRIZE_NAME["grand_prize"]
        print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（{invoice8Digits}）")
        return AwardResult(
            period=dbPeriodLabel,
            is_winning=True,
            prize_type="grand_prize",
            prize_amount=PRIZE_AMOUNT["grand_prize"],
            matched_number=grandPrize,
        )

    # C~H：頭獎到六獎，基於 first_prizes 各組比對
    if firstPrizes:
        # C. 頭獎：8 碼完全等於任一組 first_prize
        for fp in firstPrizes:
            if invoice8Digits == fp:
                prizeName = PRIZE_NAME["first_prize"]
                print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（{invoice8Digits}）")
                return AwardResult(
                    period=dbPeriodLabel,
                    is_winning=True,
                    prize_type="first_prize",
                    prize_amount=PRIZE_AMOUNT["first_prize"],
                    matched_number=fp,
                )

        # D. 二獎：末 7 碼與任一組 first_prize 末 7 碼相符
        invoice7 = invoice8Digits[-7:]
        for fp in firstPrizes:
            if invoice7 == fp[-7:]:
                prizeName = PRIZE_NAME["second_prize"]
                print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（命中頭獎組 {fp}）")
                return AwardResult(
                    period=dbPeriodLabel,
                    is_winning=True,
                    prize_type="second_prize",
                    prize_amount=PRIZE_AMOUNT["second_prize"],
                    matched_number=fp,  # 回傳命中的頭獎完整 8 碼
                )

        # E. 三獎：末 6 碼
        invoice6 = invoice8Digits[-6:]
        for fp in firstPrizes:
            if invoice6 == fp[-6:]:
                prizeName = PRIZE_NAME["third_prize"]
                print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（命中頭獎組 {fp}）")
                return AwardResult(
                    period=dbPeriodLabel,
                    is_winning=True,
                    prize_type="third_prize",
                    prize_amount=PRIZE_AMOUNT["third_prize"],
                    matched_number=fp,
                )

        # F. 四獎：末 5 碼
        invoice5 = invoice8Digits[-5:]
        for fp in firstPrizes:
            if invoice5 == fp[-5:]:
                prizeName = PRIZE_NAME["fourth_prize"]
                print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（命中頭獎組 {fp}）")
                return AwardResult(
                    period=dbPeriodLabel,
                    is_winning=True,
                    prize_type="fourth_prize",
                    prize_amount=PRIZE_AMOUNT["fourth_prize"],
                    matched_number=fp,
                )

        # G. 五獎：末 4 碼
        invoice4 = invoice8Digits[-4:]
        for fp in firstPrizes:
            if invoice4 == fp[-4:]:
                prizeName = PRIZE_NAME["fifth_prize"]
                print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（命中頭獎組 {fp}）")
                return AwardResult(
                    period=dbPeriodLabel,
                    is_winning=True,
                    prize_type="fifth_prize",
                    prize_amount=PRIZE_AMOUNT["fifth_prize"],
                    matched_number=fp,
                )

        # H. 六獎：末 3 碼
        invoice3 = invoice8Digits[-3:]
        for fp in firstPrizes:
            if invoice3 == fp[-3:]:
                prizeName = PRIZE_NAME["sixth_prize"]
                print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（命中頭獎組 {fp}）")
                return AwardResult(
                    period=dbPeriodLabel,
                    is_winning=True,
                    prize_type="sixth_prize",
                    prize_amount=PRIZE_AMOUNT["sixth_prize"],
                    matched_number=fp,
                )

    # I. 增開六獎：末 3 碼與任一組 additional_sixth_prizes 完全相等
    if additionalSixthPrizes:
        invoice3 = invoice8Digits[-3:]
        for asp in additionalSixthPrizes:
            if invoice3 == asp:
                prizeName = PRIZE_NAME["additional_sixth_prize"]
                print(f"[Prize] {dbPeriodLabel} 中獎！{prizeName}（{invoice3}）")
                return AwardResult(
                    period=dbPeriodLabel,
                    is_winning=True,
                    prize_type="additional_sixth_prize",
                    prize_amount=PRIZE_AMOUNT["additional_sixth_prize"],
                    matched_number=asp,  # 回傳命中的 3 碼增開六獎號碼
                )

    # 完全未中獎
    print(f"[Prize] {dbPeriodLabel} 未中獎（{invoice8Digits}）")
    return AwardResult(
        period=dbPeriodLabel,
        is_winning=False,
        prize_type=None,
        prize_amount=0,
        matched_number=None,
    )
