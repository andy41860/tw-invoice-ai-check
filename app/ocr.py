# -*- coding: utf-8 -*-
"""
OCR 核心模組：YOLO+OCR 辨識所需的所有 helper 函式與常數。

包含：影像前處理、OCR 變體生成、欄位格式後處理、信心值評分、bbox 裁切。
邏輯與原批次腳本完全等價，函式名稱改為 PascalCase、變數名稱改為 camelCase。
"""

import re

import cv2


# YOLO class id 對應欄位名稱
CLASS_MAP = {
    0: "invoice_number",
    1: "invoice_date",
    2: "invoice_time",
    3: "total_amount",
    4: "merchant_name",
}


def FixCommonOcrMistakesForDigits(text: str) -> str:
    """
    修正常見 OCR 誤判字元（字母誤判為數字）。
    用於日期、時間、金額、發票號碼的數字區。
    """
    # 常見 OCR 誤判對照表
    mapping = {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "Z": "2",
        "G": "6",
        "Q": "0",
    }
    return "".join(mapping.get(ch, ch) for ch in text)


def IsValidTime(hh: int, mi: int, ss: int) -> bool:
    """
    檢查時間數值是否在合法範圍內（24 小時制）。
    """
    return 0 <= hh <= 23 and 0 <= mi <= 59 and 0 <= ss <= 59


def NormalizeInvoiceTime(text: str) -> str:
    """
    時間欄位專用 normalize，目標格式：HH:MM:SS。

    支援格式：
    - 07:08:35 / 7:8:35（有冒號）
    - 070835（6 碼無冒號）
    - 0708（4 碼，補秒數 00）
    """
    if not text:
        return ""

    cleanText = str(text).strip()

    # 替換常見分隔符為冒號
    cleanText = cleanText.replace("：", ":")
    cleanText = cleanText.replace(";", ":")
    cleanText = cleanText.replace(".", ":")
    cleanText = cleanText.replace("_", ":")
    cleanText = cleanText.replace("-", ":")
    cleanText = cleanText.replace(" ", "")

    cleanText = FixCommonOcrMistakesForDigits(cleanText)

    # 情境 1：有冒號，例如 07:08:35 / 7:8:35 / 07:08
    matchResult = re.search(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", cleanText)
    if matchResult:
        hh, mi, ss = matchResult.groups()
        if ss is None:
            ss = "00"
        hh, mi, ss = int(hh), int(mi), int(ss)
        if IsValidTime(hh, mi, ss):
            return f"{hh:02d}:{mi:02d}:{ss:02d}"

    # 情境 2：沒有冒號，例如 070835
    matchResult = re.search(r"(\d{2})(\d{2})(\d{2})", cleanText)
    if matchResult:
        hh, mi, ss = matchResult.groups()
        hh, mi, ss = int(hh), int(mi), int(ss)
        if IsValidTime(hh, mi, ss):
            return f"{hh:02d}:{mi:02d}:{ss:02d}"

    # 情境 3：只有時分，例如 0708
    matchResult = re.search(r"(\d{2})(\d{2})", cleanText)
    if matchResult:
        hh, mi = matchResult.groups()
        hh, mi, ss = int(hh), int(mi), 0
        if IsValidTime(hh, mi, ss):
            return f"{hh:02d}:{mi:02d}:{ss:02d}"

    return ""


def NormalizeText(fieldName: str, text: str) -> str:
    """
    依欄位名稱對 OCR 原始結果做後處理，統一格式。
    """
    if not text:
        return ""

    cleanText = str(text).strip()

    if fieldName == "invoice_number":
        cleanText = cleanText.upper()
        cleanText = cleanText.replace(" ", "")
        cleanText = cleanText.replace("—", "-")
        cleanText = cleanText.replace("–", "-")
        cleanText = cleanText.replace("_", "-")
        cleanText = cleanText.replace("－", "-")
        cleanText = cleanText.replace(":", "-")
        cleanText = cleanText.replace(".", "-")

        # 標準格式 AB-12345678
        matchResult = re.search(r"([A-Z]{2})-?(\d{8})", cleanText)
        if matchResult:
            return f"{matchResult.group(1)}-{matchResult.group(2)}"

        textNoDash = cleanText.replace("-", "")
        if len(textNoDash) >= 10:
            prefix = textNoDash[:2]
            rest = FixCommonOcrMistakesForDigits(textNoDash[2:])
            matchResult = re.search(r"([A-Z]{2})(\d{8})", prefix + rest)
            if matchResult:
                return f"{matchResult.group(1)}-{matchResult.group(2)}"

        return cleanText

    if fieldName == "invoice_date":
        cleanText = cleanText.replace("/", "-")
        cleanText = cleanText.replace(".", "-")
        cleanText = cleanText.replace("_", "-")
        cleanText = cleanText.replace("年", "-")
        cleanText = cleanText.replace("月", "-")
        cleanText = cleanText.replace("日", "")
        cleanText = cleanText.replace(" ", "")
        cleanText = FixCommonOcrMistakesForDigits(cleanText)

        matchResult = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", cleanText)
        if matchResult:
            yyyy, mm, dd = matchResult.groups()
            return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"

        matchResult = re.search(r"(\d{4})(\d{2})(\d{2})", cleanText)
        if matchResult:
            yyyy, mm, dd = matchResult.groups()
            return f"{yyyy}-{mm}-{dd}"

        return cleanText

    if fieldName == "invoice_time":
        return NormalizeInvoiceTime(cleanText)

    if fieldName == "total_amount":
        cleanText = cleanText.replace(",", "")
        cleanText = cleanText.replace("$", "")
        cleanText = cleanText.replace("＄", "")
        cleanText = cleanText.replace("元", "")
        cleanText = cleanText.replace(" ", "")
        cleanText = FixCommonOcrMistakesForDigits(cleanText)
        digits = re.findall(r"\d+", cleanText)
        if digits:
            return "".join(digits)
        return cleanText

    if fieldName == "merchant_name":
        cleanText = cleanText.replace("\n", " ")
        cleanText = re.sub(r"\s+", " ", cleanText)
        return cleanText.strip()

    return cleanText


def ScoreOcrResult(fieldName: str, text: str) -> int:
    """
    根據欄位格式幫 OCR 結果打分數，分數越高代表越接近正確格式。
    用於 MakeOcrVariants 的多版本結果中挑選最佳。
    """
    if not text:
        return 0

    t = str(text).strip().upper()
    tClean = t.replace(" ", "").replace("_", "-")

    if fieldName == "invoice_number":
        if re.search(r"[A-Z]{2}-?\d{8}", tClean):
            return 100
        digitCount = len(re.findall(r"\d", tClean))
        letterCount = len(re.findall(r"[A-Z]", tClean))
        if letterCount >= 2 and digitCount >= 6:
            return 70
        if digitCount >= 8:
            return 50
        return 10

    if fieldName == "invoice_date":
        if re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", tClean):
            return 100
        if re.search(r"\d{8}", tClean):
            return 80
        if len(re.findall(r"\d", tClean)) >= 6:
            return 50
        return 10

    if fieldName == "invoice_time":
        # 標準合法格式 HH:MM:SS
        matchResult = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", tClean)
        if matchResult:
            hh, mi, ss = map(int, matchResult.groups())
            if IsValidTime(hh, mi, ss):
                score = 100
                # 台灣商業發票時間通常在白天，輕微加權（非硬規則）
                if 6 <= hh <= 23:
                    score += 5
                return score

        matchResult = re.fullmatch(r"(\d{2}):(\d{2})", tClean)
        if matchResult:
            hh, mi = map(int, matchResult.groups())
            if IsValidTime(hh, mi, 0):
                return 80

        matchResult = re.fullmatch(r"(\d{6})", tClean)
        if matchResult:
            hh = int(tClean[0:2])
            mi = int(tClean[2:4])
            ss = int(tClean[4:6])
            if IsValidTime(hh, mi, ss):
                return 70

        return 10

    if fieldName == "total_amount":
        digits = re.findall(r"\d", tClean)
        if 1 <= len(digits) <= 8:
            return 80 + len(digits)
        return 10

    if fieldName == "merchant_name":
        return min(len(tClean), 100)

    return 0


def SortEasyocrDetailResultLeftToRight(result: list) -> list:
    """
    EasyOCR detail=1 回傳的結果依 bbox 最小 x 座標由左至右排序。
    用於避免時間片段（如 07:08:35）順序顛倒。

    EasyOCR detail=1 格式：[[bbox, text, confidence], ...]
    bbox 為四個點：[[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
    """
    def GetMinX(item):
        bbox = item[0]
        return min(point[0] for point in bbox)

    return sorted(result, key=GetMinX)


def JoinEasyocrDetailResult(result: list) -> str:
    """
    將 EasyOCR detail=1 的結果由左至右串接為單一字串。
    """
    if not result:
        return ""

    sortedResult = SortEasyocrDetailResultLeftToRight(result)
    texts = [str(item[1]).strip() for item in sortedResult if str(item[1]).strip()]
    return "".join(texts)


def EasyocrReadByField(ocrReader, image, fieldName: str):
    """
    依照欄位類型設定 EasyOCR 的 allowlist 與解碼參數執行 OCR。

    特別注意：
    - invoice_time 使用 detail=1 保留 bbox 座標，再依 x 座標排序避免順序錯誤。
    - 其他欄位使用 detail=0 直接回傳文字 list。
    """
    if fieldName == "invoice_number":
        return ocrReader.readtext(
            image,
            detail=0,
            paragraph=False,
            allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
            decoder="beamsearch",
            beamWidth=10,
        )

    if fieldName == "invoice_date":
        return ocrReader.readtext(
            image,
            detail=0,
            paragraph=False,
            allowlist="0123456789-/.年月日",
            decoder="beamsearch",
            beamWidth=10,
        )

    if fieldName == "invoice_time":
        # detail=1 保留座標，排序後串接，避免時間片段順序顛倒
        result = ocrReader.readtext(
            image,
            detail=1,
            paragraph=False,
            allowlist="0123456789:",
            decoder="beamsearch",
            beamWidth=10,
        )
        return JoinEasyocrDetailResult(result)

    if fieldName == "total_amount":
        return ocrReader.readtext(
            image,
            detail=0,
            paragraph=False,
            allowlist="0123456789,$＄元",
            decoder="beamsearch",
            beamWidth=10,
        )

    # merchant_name 及其他欄位：不限 allowlist
    return ocrReader.readtext(
        image,
        detail=0,
        paragraph=False,
        decoder="beamsearch",
        beamWidth=5,
    )


def ParseOcrRawResultToText(result, fieldName: str) -> str:
    """
    統一處理 EasyOCR 回傳結果為純文字字串。

    - invoice_time：EasyocrReadByField 已回傳排序後字串，直接轉換。
    - 其他欄位：回傳 list[str]，用空白串接。
    """
    if not result:
        return ""

    if fieldName == "invoice_time":
        return str(result).strip()

    if isinstance(result, str):
        return result.strip()

    return " ".join([str(t).strip() for t in result if str(t).strip()])


def MakeOcrVariants(crop, fieldName: str = None, scale: int = 4) -> list:
    """
    針對同一個 crop 產生多種 OpenCV 前處理版本供 OCR 逐一嘗試，最後挑最佳結果。

    版本包含：
    - original_enlarged：放大（CUBIC 插值）
    - gray_denoise：灰階去雜訊（商家名稱除外，過度處理易破壞中文字）
    - clahe：CLAHE 對比度增強
    """
    if crop is None or crop.size == 0:
        return []

    variants = []

    # 放大 crop，提升 OCR 辨識率
    enlarged = cv2.resize(
        crop,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )
    variants.append(("original_enlarged", enlarged))

    # 商家名稱保留原圖，中文字過度二值化容易損壞
    if fieldName == "merchant_name":
        return variants

    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)

    # 快速去雜訊
    denoise = cv2.fastNlMeansDenoising(
        gray,
        None,
        h=8,
        templateWindowSize=7,
        searchWindowSize=21,
    )
    variants.append(("gray_denoise", denoise))

    # CLAHE 對比度增強，幫助辨識低對比文字
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    contrast = clahe.apply(denoise)
    variants.append(("clahe", contrast))

    return variants


def EasyocrTextBest(
    ocrReader,
    crop,
    fieldName: str = None,
    saveDebugDir=None,
    imageStem: str = None,
) -> str:
    """
    產生多種 OpenCV 前處理版本，逐一 OCR，挑出最符合欄位格式的結果。

    Args:
        ocrReader: EasyOCR Reader 實例。
        crop: 從 YOLO bbox 裁切出的圖片區塊（BGR ndarray）。
        fieldName: 欄位名稱，用於選擇 allowlist 與評分規則。
        saveDebugDir: debug 輸出目錄（None 表示不輸出）。
        imageStem: 圖片 stem（debug 用途）。

    Returns:
        最高分的 OCR 結果字串（已經過 NormalizeText 後處理）。
    """
    variants = MakeOcrVariants(crop=crop, fieldName=fieldName, scale=4)

    bestText = ""
    bestScore = -1
    bestVariantName = ""

    for variantName, variantImg in variants:

        # 若指定 debug 目錄則儲存前處理圖片
        if saveDebugDir is not None and imageStem is not None:
            saveDebugDir.mkdir(parents=True, exist_ok=True)
            debugName = f"{imageStem}_{fieldName}_{variantName}.jpg"
            cv2.imwrite(str(saveDebugDir / debugName), variantImg)

        try:
            result = EasyocrReadByField(
                ocrReader=ocrReader,
                image=variantImg,
                fieldName=fieldName,
            )
        except Exception as ocrError:
            print(f"[OCR][WARN] OCR 失敗：{fieldName} / {variantName} / {ocrError}")
            continue

        rawText = ParseOcrRawResultToText(result, fieldName)
        cleanText = NormalizeText(fieldName, rawText)
        score = ScoreOcrResult(fieldName, cleanText)

        if score > bestScore:
            bestScore = score
            bestText = cleanText
            bestVariantName = variantName

    if bestText:
        print(f"[OCR] {fieldName}: {bestText} ({bestVariantName}, score={bestScore})")
    else:
        print(f"[OCR] {fieldName}: 無法辨識")

    return bestText


def CropBbox(image, xyxy, padRatio: float = 0.15, padXRatio: float = None, padYRatio: float = None):
    """
    根據 YOLO bbox 裁切圖片區塊，並加入 padding 避免邊緣被截斷。

    Args:
        image: 原始 BGR ndarray。
        xyxy: YOLO bbox 座標 [x1, y1, x2, y2]。
        padRatio: x/y 共用 padding 比例（預設 0.15）。
        padXRatio: x 方向 padding 比例（None 時使用 padRatio）。
        padYRatio: y 方向 padding 比例（None 時使用 padRatio）。

    Returns:
        裁切後的 BGR ndarray，或 None（bbox 無效時）。
    """
    h, w = image.shape[:2]
    x1, y1, x2, y2 = map(float, xyxy)

    boxW = x2 - x1
    boxH = y2 - y1

    if padXRatio is None:
        padXRatio = padRatio
    if padYRatio is None:
        padYRatio = padRatio

    padX = boxW * padXRatio
    padY = boxH * padYRatio

    x1 = max(0, int(x1 - padX))
    y1 = max(0, int(y1 - padY))
    x2 = min(w, int(x2 + padX))
    y2 = min(h, int(y2 + padY))

    if x2 <= x1 or y2 <= y1:
        return None

    return image[y1:y2, x1:x2]


def GetBestBoxesByClass(yoloResult) -> dict:
    """
    從 YOLO 推論結果中，每個 class 只取 confidence 最高的 bbox。

    Args:
        yoloResult: ultralytics YOLO 單張推論結果物件。

    Returns:
        dict，key 為 class_id，value 為 {"conf": float, "xyxy": ndarray}。
    """
    best = {}
    boxes = yoloResult.boxes

    if boxes is None or len(boxes) == 0:
        return best

    for box in boxes:
        classId = int(box.cls[0].item())
        conf = float(box.conf[0].item())
        xyxy = box.xyxy[0].cpu().numpy()

        if classId not in CLASS_MAP:
            continue

        if classId not in best or conf > best[classId]["conf"]:
            best[classId] = {
                "conf": conf,
                "xyxy": xyxy,
            }

    return best
