# -*- coding: utf-8 -*-
"""
共用工具函數模組。
負責：影像 bytes 解碼、OCR 字串 → Python 型別轉換。
"""

import re
from datetime import date, time
from typing import Optional

import cv2
import numpy as np


def DecodeImageBytes(rawBytes: bytes) -> Optional[np.ndarray]:
    """
    將上傳的圖片 bytes 解碼為 OpenCV BGR ndarray。

    Args:
        rawBytes: 圖片原始 bytes（jpg / png / bmp 等格式）。

    Returns:
        解碼成功回 BGR ndarray；解碼失敗（非合法圖片）回 None。
    """
    # 把 bytes 轉成 uint8 ndarray，再用 cv2 解碼
    imgArray = np.frombuffer(rawBytes, dtype=np.uint8)
    imgBgr = cv2.imdecode(imgArray, cv2.IMREAD_COLOR)
    return imgBgr  # 若解碼失敗，imdecode 回傳 None


def CleanInvoiceNumber(text: str) -> Optional[str]:
    """
    清理 OCR 辨識出的發票號碼字串。
    空字串或無法構成合法發票號碼格式時回 None。

    Args:
        text: OCR 原始文字，例如 "AB-12345678" / "" 等。

    Returns:
        合法字串（保留原格式）或 None。
    """
    if not text or not text.strip():
        return None
    return text.strip()


def ParseInvoiceDate(text: str) -> Optional[date]:
    """
    將 OCR 辨識出的日期字串轉換為 date 物件。
    ocr.NormalizeText 已將日期標準化為 "YYYY-MM-DD"。

    Args:
        text: normalize 後的日期字串，例如 "2026-01-15"。

    Returns:
        date 物件或 None（格式不合法或空字串）。
    """
    if not text or not text.strip():
        return None

    # 嘗試解析 YYYY-MM-DD 格式
    matchResult = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text.strip())
    if matchResult:
        try:
            yyyy = int(matchResult.group(1))
            mm = int(matchResult.group(2))
            dd = int(matchResult.group(3))
            return date(yyyy, mm, dd)
        except ValueError:
            # 日期值超出範圍（例如月份 13）
            return None

    return None


def ParseInvoiceTime(text: str) -> Optional[time]:
    """
    將 OCR 辨識出的時間字串轉換為 time 物件。
    ocr.NormalizeText 已將時間標準化為 "HH:MM:SS"。

    Args:
        text: normalize 後的時間字串，例如 "13:45:22"。

    Returns:
        time 物件或 None（格式不合法或空字串）。
    """
    if not text or not text.strip():
        return None

    # 嘗試解析 HH:MM:SS 格式
    matchResult = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", text.strip())
    if matchResult:
        try:
            hh = int(matchResult.group(1))
            mi = int(matchResult.group(2))
            ss = int(matchResult.group(3))
            return time(hh, mi, ss)
        except ValueError:
            # 時間值超出範圍
            return None

    return None


def ParseTotalAmount(text: str) -> Optional[int]:
    """
    將 OCR 辨識出的金額字串轉換為整數。
    ocr.NormalizeText 已移除千分位、幣符號等，
    留下純數字字串。

    Args:
        text: normalize 後的金額字串，例如 "120"。

    Returns:
        整數金額或 None（空字串或非純數字）。
    """
    if not text or not text.strip():
        return None

    cleanText = text.strip()
    # 只接受純數字（normalize 後應已過濾）
    if re.fullmatch(r"\d+", cleanText):
        return int(cleanText)

    return None
