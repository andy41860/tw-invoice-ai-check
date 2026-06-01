# -*- coding: utf-8 -*-
"""
Pydantic 輸入/輸出模型。
嚴格對齊 api_schema.txt 定義的欄位名稱、型別與巢狀結構。
"""

from datetime import date, time
from typing import Optional

from pydantic import BaseModel, ConfigDict


class InvoiceData(BaseModel):
    """
    OCR 解析出的發票欄位資料。
    OCR 無法辨識的欄位回傳 None（不回傳空字串）。
    """

    model_config = ConfigDict(
        # date 序列化為 "YYYY-MM-DD"，time 序列化為 "HH:MM:SS"
        json_encoders={
            date: lambda v: v.isoformat(),
            time: lambda v: v.strftime("%H:%M:%S"),
        }
    )

    # 發票號碼，格式建議 "AB-12345678"；保留前導 0，不可轉 int
    invoice_number: Optional[str] = None
    # 發票日期，西元格式 "YYYY-MM-DD"
    invoice_date: Optional[date] = None
    # 發票時間，格式 "HH:MM:SS"；抓不到時為 None
    invoice_time: Optional[time] = None
    # 發票總金額，單位新台幣元
    total_amount: Optional[int] = None
    # 商家名稱
    merchant_name: Optional[str] = None


class YoloConfidence(BaseModel):
    """
    YOLO 偵測各欄位的最高 confidence（0~1）。
    未偵測到時回傳 None（區別「沒偵測到」vs「信心值為 0」）。
    """

    # 發票號碼偵測信心值
    invoice_number: Optional[float] = None
    # 發票日期偵測信心值
    invoice_date: Optional[float] = None
    # 發票時間偵測信心值
    invoice_time: Optional[float] = None
    # 金額偵測信心值
    total_amount: Optional[float] = None
    # 商家名稱偵測信心值
    merchant_name: Optional[float] = None


class AwardResult(BaseModel):
    """
    發票對獎結果。嚴格對齊 api_schema.txt 的 5 個欄位。
    """

    # 發票所屬期別，例如 "115年01-02月"；invoice_date 無效時為 None
    period: Optional[str] = None
    # 是否中獎
    is_winning: bool = False
    # 中獎獎項類型（英文 key）；未中獎為 None
    # 可用值: special_prize / grand_prize / first_prize / second_prize /
    #         third_prize / fourth_prize / fifth_prize / sixth_prize /
    #         additional_sixth_prize
    prize_type: Optional[str] = None
    # 中獎金額（新台幣元）；未中獎固定為 0
    prize_amount: int = 0
    # 命中的中獎號碼；未中獎為 None
    matched_number: Optional[str] = None


class InvoiceAnalyzeData(BaseModel):
    """
    推論結果主體，包含圖片資訊、OCR 發票欄位、YOLO 信心值與對獎結果。
    """

    # 原始圖片檔名
    image_name: str
    # 本次推論使用的 YOLO 權重版本
    model_version: str
    # OCR 解析的發票資訊
    invoice: InvoiceData
    # YOLO 偵測各欄位的信心值
    yolo_confidence: YoloConfidence
    # 對獎結果
    award_result: AwardResult


class InvoiceAnalyzeResponse(BaseModel):
    """
    API 最外層回應結構。
    success=True 表示推論流程正常完成；圖片無法讀取或服務異常時為 False。
    """

    # API 是否成功完成推論流程
    success: bool
    # 本次請求唯一識別碼，用於追蹤推論與 DB 寫入記錄
    request_id: str
    # 推論結果（success=False 時為 None）
    data: Optional[InvoiceAnalyzeData] = None
