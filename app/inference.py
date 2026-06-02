# -*- coding: utf-8 -*-
"""
推論模組：封裝 YOLO+OCR 邏輯，供 FastAPI route handler 使用。

OCR 核心邏輯定義於 app/ocr.py。
此模組負責：
- 服務啟動時一次性載入模型（LoadModels）
- 將 API 傳入的圖片 bytes 轉換後執行 YOLO+OCR 推論（AnalyzeImage）

與原批次腳本的推論流程完全等價，差異僅有：
  (a) 輸入改為記憶體中的 BGR ndarray（由 API 接收 bytes 後解碼）。
  (b) 額外回傳每個欄位的 YOLO confidence，供 yolo_confidence 欄位使用。
"""

import threading
from typing import Any, Dict, Optional

import easyocr
from ultralytics import YOLO

from app.config import Settings
from app.ocr import (
    CLASS_MAP,
    CropBbox,
    EasyocrTextBest,
    GetBestBoxesByClass,
)


def LoadModels(settings: Settings) -> Dict[str, Any]:
    """
    在服務啟動時一次性載入 YOLO 模型與 EasyOCR 模型。
    放入 app.state，後續透過 DI 注入，不在每次 request 重新載入。

    Args:
        settings: 型別化設定物件（含權重路徑、device 等）。

    Returns:
        dict 包含：
            - "yolo": YOLO model 實例
            - "ocrReaders": {"en": EasyOCR Reader, "zh": EasyOCR Reader}
    """
    weightPath = settings.ModelWeightPath

    if not weightPath.exists():
        raise FileNotFoundError(
            f"找不到 YOLO 權重檔：{weightPath}，"
            f"請確認 model/{settings.ModelVersion}/{settings.ModelWeightName} 已放置正確。"
        )

    print(f"[Inference] 載入 YOLO 模型：{weightPath}")
    yoloModel = YOLO(str(weightPath))

    print("[Inference] 載入 EasyOCR 英文模型 ...")
    ocrEn = easyocr.Reader(
        ["en"],
        gpu=False,      # 固定 CPU 推論
        verbose=False,
    )

    print("[Inference] 載入 EasyOCR 中文模型 ...")
    ocrZh = easyocr.Reader(
        ["ch_tra", "en"],
        gpu=False,      # 固定 CPU 推論
        verbose=False,
    )

    print("[Inference] 模型載入完成。")
    return {
        "yolo": yoloModel,
        "ocrReaders": {
            "en": ocrEn,
            "zh": ocrZh,
        },
        # 保護 yoloModel.predict() 避免多 thread 同時進入造成 fuse race condition
        "yolo_lock": threading.Lock(),
    }


def AnalyzeImage(
    imageBgr,
    yoloModel: Any,
    ocrReaders: Dict[str, Any],
    conf: float,
    imgsz: int,
    yoloLock: threading.Lock = None,
) -> Optional[Dict[str, Any]]:
    """
    對單張發票圖片執行 YOLO 偵測 + EasyOCR 辨識，回傳欄位結果與信心值。

    流程（與原批次腳本推論完全等價）：
    1. YOLO 偵測 bbox（device=cpu）
    2. 每個欄位取最高 confidence bbox（GetBestBoxesByClass）
    3. CropBbox 裁切區塊（時間欄位加大 x padding）
    4. MakeOcrVariants 產生多種前處理版本（EasyocrTextBest 內部呼叫）
    5. EasyOCR 多版本辨識、依欄位格式挑最佳結果（EasyocrTextBest）
    6. NormalizeText 後處理（EasyocrTextBest 內部呼叫）

    Args:
        imageBgr: OpenCV BGR ndarray，由 utils.DecodeImageBytes 解碼取得。
        yoloModel: 已載入的 YOLO 模型實例。
        ocrReaders: dict，{"en": EasyOCR Reader, "zh": EasyOCR Reader}。
        conf: YOLO confidence threshold。
        imgsz: YOLO 推論圖片尺寸。

    Returns:
        dict 包含：
            - "fields": {"invoice_number": str, ...}（5 個欄位，辨識不到為 ""）
            - "confidence": {"invoice_number": float|None, ...}（未偵測到為 None）
        YOLO 推論失敗時回傳 None。
    """
    # 初始化欄位結果（辨識不到預設 ""）
    fields: Dict[str, str] = {fieldName: "" for fieldName in CLASS_MAP.values()}
    confidence: Dict[str, Optional[float]] = {fieldName: None for fieldName in CLASS_MAP.values()}

    # Step 1：YOLO 推論（device=cpu）
    # 用 Lock 確保同一時間只有一個 thread 進入 predict()，
    # 防止 Ultralytics 首次 predict 的 lazy fuse 在多執行緒下發生 race condition。
    try:
        if yoloLock is not None:
            with yoloLock:
                results = yoloModel.predict(
                    source=imageBgr,
                    device="cpu",
                    conf=conf,
                    imgsz=imgsz,
                    verbose=False,
                )
        else:
            results = yoloModel.predict(
                source=imageBgr,
                device="cpu",
                conf=conf,
                imgsz=imgsz,
                verbose=False,
            )
    except Exception as inferenceError:
        print(f"[Inference][WARN] YOLO 推論失敗：{inferenceError}")
        return None

    if not results:
        return {"fields": fields, "confidence": confidence}

    yoloResult = results[0]

    # Step 2：每個 class 取最高 confidence bbox
    bestBoxes = GetBestBoxesByClass(yoloResult)

    # 收集各欄位的 YOLO confidence
    for classId, fieldName in CLASS_MAP.items():
        if classId in bestBoxes:
            confidence[fieldName] = bestBoxes[classId]["conf"]

    # Step 3-6：對每個欄位 crop + OCR
    for classId, fieldName in CLASS_MAP.items():

        if classId not in bestBoxes:
            fields[fieldName] = ""
            continue

        # 時間欄位橫向 padding 加大，避免時間被切太窄
        if fieldName == "invoice_time":
            crop = CropBbox(
                image=imageBgr,
                xyxy=bestBoxes[classId]["xyxy"],
                padXRatio=0.35,
                padYRatio=0.20,
            )
        else:
            crop = CropBbox(
                image=imageBgr,
                xyxy=bestBoxes[classId]["xyxy"],
                padRatio=0.15,
            )

        if crop is None:
            fields[fieldName] = ""
            continue

        # 商家名稱用中文 reader；其餘用英文 reader
        if fieldName == "merchant_name":
            ocrReader = ocrReaders["zh"]
        else:
            ocrReader = ocrReaders["en"]

        # 多版本前處理 + OCR + 挑最佳結果
        bestText = EasyocrTextBest(
            ocrReader=ocrReader,
            crop=crop,
            fieldName=fieldName,
        )

        fields[fieldName] = bestText

    return {
        "fields": fields,
        "confidence": confidence,
    }
