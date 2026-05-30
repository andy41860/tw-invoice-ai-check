# -*- coding: utf-8 -*-
"""
API 測試腳本：讀取 test_samples/ 資料夾內所有圖片，
逐一呼叫 POST /analyze，印出完整 JSON 回應。

前置條件：docker compose up -d（服務需先啟動）

使用方式：
    uv run python test_inference.py
    uv run python test_inference.py --image-dir test_samples
    uv run python test_inference.py --api-url http://localhost:8000
"""

import os

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import argparse
import json
import sys
from pathlib import Path

import requests


def ParseArgs():
    """解析命令列參數。"""
    parser = argparse.ArgumentParser(
        description="發票 OCR 對獎 API 測試腳本",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8000",
        help="FastAPI 服務位址（預設：http://localhost:8000）",
    )
    parser.add_argument(
        "--image-dir",
        type=str,
        default="test_samples",
        help="圖片資料夾路徑（預設：test_samples）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="單次請求 timeout 秒數（預設：120，推論較耗時）",
    )
    return parser.parse_args()


def ListImages(imageDir: Path) -> list:
    """列出資料夾內所有支援格式的圖片（jpg / jpeg / png / bmp / webp）。"""
    supportedExts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    return sorted([
        p for p in imageDir.rglob("*")
        if p.suffix.lower() in supportedExts
    ])


def CheckServiceHealth(apiUrl: str, timeout: int) -> bool:
    """
    呼叫 GET /health 確認服務是否正常運作。
    回傳 True 表示服務存活。
    """
    try:
        resp = requests.get(f"{apiUrl}/health", timeout=timeout)
        return resp.status_code == 200
    except requests.exceptions.ConnectionError:
        return False


def AnalyzeImage(apiUrl: str, imagePath: Path, timeout: int) -> dict:
    """
    呼叫 POST /analyze 上傳圖片，回傳完整 JSON dict。
    發生錯誤時回傳含 error 欄位的 dict。
    """
    with open(imagePath, "rb") as imageFile:
        resp = requests.post(
            f"{apiUrl}/analyze",
            files={"file": (imagePath.name, imageFile, "image/jpeg")},
            timeout=timeout,
        )

    if resp.status_code == 200:
        return resp.json()
    else:
        return {
            "success": False,
            "error": f"HTTP {resp.status_code}",
            "detail": resp.text,
        }


def main():
    args = ParseArgs()

    imageDir = Path(args.image_dir)
    apiUrl   = args.api_url.rstrip("/")

    # ---- 驗證圖片資料夾 ----
    if not imageDir.exists():
        print(f"[ERROR] 找不到圖片資料夾：{imageDir}", file=sys.stderr)
        sys.exit(1)

    imagePaths = ListImages(imageDir)
    if not imagePaths:
        print(f"[WARN] 資料夾內沒有圖片：{imageDir}", file=sys.stderr)
        sys.exit(0)

    # ---- 確認服務是否啟動 ----
    print(f"[INFO] 確認服務狀態：{apiUrl}/health")
    if not CheckServiceHealth(apiUrl, timeout=5):
        print(f"[ERROR] 無法連接服務 {apiUrl}，請確認 docker compose up -d 已執行", file=sys.stderr)
        sys.exit(1)
    print("[INFO] 服務正常\n")

    # ---- 逐張呼叫 API ----
    for imagePath in imagePaths:
        print(f"{'='*60}")
        print(f"[INFO] 上傳：{imagePath.name}")
        print(f"{'='*60}")

        try:
            result = AnalyzeImage(apiUrl, imagePath, args.timeout)
        except requests.exceptions.Timeout:
            print(f"[WARN] 請求逾時（>{args.timeout}s）：{imagePath.name}\n")
            continue
        except requests.exceptions.ConnectionError as connErr:
            print(f"[WARN] 連線錯誤：{connErr}\n")
            continue

        print(json.dumps(result, ensure_ascii=False, indent=2))
        print()

    print(f"[INFO] 完成，共處理 {len(imagePaths)} 張圖片")


if __name__ == "__main__":
    main()
