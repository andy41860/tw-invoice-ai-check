# -*- coding: utf-8 -*-
"""
FastAPI 主程式：lifespan 管理模型載入、路由定義。

路由：
- GET  /health   → 健康檢查
- POST /analyze  → 上傳 jpg → YOLO+OCR → 對獎 → 回傳 JSON

重要：
- /analyze 使用同步 def（非 async），讓 FastAPI 丟到 threadpool 執行，
  避免 CPU 密集的 YOLO/EasyOCR 推論卡住 event loop。
- 模型只在 lifespan 啟動載入一次，透過 Depends(get_models) 注入。
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

import psycopg
from fastapi import Depends, FastAPI, File, UploadFile, status
from fastapi.responses import JSONResponse

from app.config import GetSettings, Settings
from app.db import CreatePool, InsertInferenceConf, InsertInferenceResult
from app.dependencies import get_db, get_models, get_settings
from app.inference import AnalyzeImage, LoadModels
from app.prize import CheckInvoiceAward
from app.schemas import (
    AwardResult,
    InvoiceAnalyzeData,
    InvoiceAnalyzeResponse,
    InvoiceData,
    YoloConfidence,
)
from app.utils import (
    CleanInvoiceNumber,
    DecodeImageBytes,
    ParseInvoiceDate,
    ParseInvoiceTime,
    ParseTotalAmount,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan：服務啟動時載入模型與建立 DB 連線池；關閉時釋放資源。
    """
    settings: Settings = GetSettings()

    # ---- 啟動：載入模型 ----
    print("[Lifespan] 服務啟動，載入模型中 ...")
    app.state.models = LoadModels(settings)
    print("[Lifespan] 模型載入完成。")

    # ---- 啟動：建立 DB 連線池 ----
    print("[Lifespan] 建立 PostgreSQL 連線池 ...")
    app.state.db_pool = CreatePool(settings)
    print("[Lifespan] 連線池建立完成。")

    yield  # 服務運行中

    # ---- 關閉：釋放資源 ----
    print("[Lifespan] 服務關閉，釋放連線池 ...")
    app.state.db_pool.close()
    print("[Lifespan] 資源釋放完成。")


app = FastAPI(
    title="發票 OCR 對獎服務",
    description="上傳台灣電子發票 jpg → YOLO+OCR 辨識 → 對獎 → 回傳 JSON",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", summary="健康檢查", tags=["系統"])
def HealthCheck():
    """
    服務健康檢查端點。
    docker compose healthcheck 與監控系統可使用此端點確認服務是否存活。
    """
    return {"status": "ok"}


@app.post(
    "/analyze",
    response_model=InvoiceAnalyzeResponse,
    summary="發票 OCR + 對獎",
    tags=["推論"],
)
def AnalyzeInvoice(
    file: UploadFile = File(..., description="發票圖片（jpg/png）"),
    models: Dict[str, Any] = Depends(get_models),
    settings: Settings = Depends(get_settings),
    conn: psycopg.Connection = Depends(get_db),
):
    """
    上傳發票圖片，回傳 OCR 辨識結果與對獎結果。

    流程：
    1. 產生本次請求的唯一 request_id。
    2. 讀取上傳的圖片 bytes，用 cv2 解碼為 BGR ndarray。
    3. 呼叫 inference.AnalyzeImage（封裝既有 YOLO+OCR 機制）。
    4. 將 OCR 字串轉換為 Pydantic 型別（date / time / int）。
    5. 呼叫 prize.CheckInvoiceAward 對獎。
    6. 將推論結果與 confidence 寫入 DB（帶入同一個 request_id）。
    7. 組裝 InvoiceAnalyzeResponse 回傳（含 request_id）。

    OCR 失敗或欄位解析失敗不回傳 500，以 success=True 但欄位為 null 表達。
    圖片無法讀取（非合法圖片格式）回傳 HTTP 400。
    """
    # ---- Step 1：產生本次請求唯一 request_id ----
    requestId = str(uuid.uuid4())

    # ---- Step 2：讀取並解碼圖片 ----
    rawBytes = file.file.read()
    imageName = file.filename or "unknown.jpg"

    imageBgr = DecodeImageBytes(rawBytes)
    if imageBgr is None:
        # 圖片格式不合法，無法解碼
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "request_id": requestId,
                "error": f"無法解碼圖片 '{imageName}'，請確認為合法的 jpg/png 格式。",
            },
        )

    # ---- Step 3：YOLO+OCR 推論（封裝既有機制，機制不變）----
    inferResult = AnalyzeImage(
        imageBgr=imageBgr,
        yoloModel=models["yolo"],
        ocrReaders=models["ocrReaders"],
        conf=settings.YoloConf,
        imgsz=settings.YoloImgsz,
    )

    if inferResult is None:
        # YOLO 推論本身異常（記憶體錯誤等）
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "request_id": requestId,
                "error": "YOLO 推論失敗，請確認圖片內容是否為發票。",
            },
        )

    # ---- Step 4：OCR 字串 → Pydantic 型別 ----
    ocrFields = inferResult["fields"]
    ocrConf = inferResult["confidence"]

    # 空字串 → None（api_schema.txt 要求）
    parsedInvoiceNumber = CleanInvoiceNumber(ocrFields.get("invoice_number", ""))
    parsedInvoiceDate = ParseInvoiceDate(ocrFields.get("invoice_date", ""))
    parsedInvoiceTime = ParseInvoiceTime(ocrFields.get("invoice_time", ""))
    parsedTotalAmount = ParseTotalAmount(ocrFields.get("total_amount", ""))
    parsedMerchantName = ocrFields.get("merchant_name") or None  # 空字串 → None

    # 組 InvoiceData
    invoiceData = InvoiceData(
        invoice_number=parsedInvoiceNumber,
        invoice_date=parsedInvoiceDate,
        invoice_time=parsedInvoiceTime,
        total_amount=parsedTotalAmount,
        merchant_name=parsedMerchantName,
    )

    # 組 YoloConfidence（None = 未偵測到）
    yoloConf = YoloConfidence(
        invoice_number=ocrConf.get("invoice_number"),
        invoice_date=ocrConf.get("invoice_date"),
        invoice_time=ocrConf.get("invoice_time"),
        total_amount=ocrConf.get("total_amount"),
        merchant_name=ocrConf.get("merchant_name"),
    )

    # ---- Step 5：對獎 ----
    awardResult: AwardResult = CheckInvoiceAward(
        invoiceNumber=parsedInvoiceNumber,
        invoiceDate=parsedInvoiceDate,
        conn=conn,
    )

    # ---- Step 6：寫入 DB（同一個 request_id 用於兩張表）----
    inferenceResultId = InsertInferenceResult(
        conn=conn,
        requestId=requestId,
        imageName=imageName,
        modelVersion=settings.ModelVersion,
        invoiceNumber=parsedInvoiceNumber,
        invoiceDate=parsedInvoiceDate,
        invoiceTime=parsedInvoiceTime,
        totalAmount=parsedTotalAmount,
        merchantName=parsedMerchantName,
        awardPeriod=awardResult.period,
        isWinning=awardResult.is_winning,
        prizeType=awardResult.prize_type,
        prizeAmount=awardResult.prize_amount,
        matchedNumber=awardResult.matched_number,
    )

    InsertInferenceConf(
        conn=conn,
        requestId=requestId,
        inferenceResultId=inferenceResultId,
        imageName=imageName,
        confidenceFields=[
            ("invoice_number", ocrConf.get("invoice_number")),
            ("invoice_date", ocrConf.get("invoice_date")),
            ("invoice_time", ocrConf.get("invoice_time")),
            ("total_amount", ocrConf.get("total_amount")),
            ("merchant_name", ocrConf.get("merchant_name")),
        ],
    )

    # ---- Step 7：組裝回應 ----
    analyzeData = InvoiceAnalyzeData(
        image_name=imageName,
        model_version=settings.ModelVersion,
        invoice=invoiceData,
        yolo_confidence=yoloConf,
        award_result=awardResult,
    )

    return InvoiceAnalyzeResponse(
        success=True,
        request_id=requestId,
        data=analyzeData,
    )
