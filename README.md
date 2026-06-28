# Taiwan Invoice AI System

此專案是一個紙本台灣電子發票證明聯 AI 自動辨識與對獎分析系統。專案使用 YOLO11 偵測發票關鍵欄位，結合 OpenCV 前處理與 OCR 擷取文字，解析發票號碼、日期、金額與商家資訊，並自動完成對獎。系統透過 FastAPI 封裝推論服務，使用 Go Batch 進行併發批次推論，並將結果寫入 PostgreSQL，最後以 Power BI 進行視覺化分析。

*註1：本專案部分程式碼與 Claude Code 協作開發。*  

*註2：由於紙本發票版型與影像拍攝品質可能不同，目前 YOLO/OCR 辨識仍有機會出現誤差。系統保留信心值與推論紀錄，方便後續追蹤與改善。*

---

## 專案結構

```text
tw-invoice-ai-check/
├── app/                       # FastAPI 推論服務核心
│   ├── main.py                # 路由定義與 lifespan 模型載入
│   ├── config.py              # 環境變數設定
│   ├── inference.py           # YOLO + EasyOCR 推論邏輯
│   ├── ocr.py                 # OCR 文字後處理
│   ├── prize.py               # 對獎判斷邏輯
│   ├── schemas.py             # Pydantic 輸入/輸出 schema
│   ├── db.py                  # PostgreSQL 連線池建立
│   ├── dependencies.py        # FastAPI DI（模型、DB、設定注入）
│   └── utils.py               # 工具函數（日期、金額解析等）
├── go_batch/                  # Go 批次推論程式（非常駐，跑完即結束）
│   ├── main.go                # 掃描圖片 → 呼叫 API → 寫入 PostgreSQL
│   ├── go.mod                 # Go module 定義
│   └── Dockerfile             # Go 容器建置
├── model/                     # YOLO 權重目錄（不進 git）
│   └── v1.0/
│       └── best.pt            # YOLO11 訓練權重
├── test_samples/              # 測試用發票圖片
├── docker-compose.yml         # 容器服務設定（api / db / batch）
├── init.sql                   # PostgreSQL 初始化腳本（建表 + 載入對獎號碼）
├── Dockerfile                 # FastAPI 容器建置
└── README.md                  # 專案說明文件
```

---

## 使用方式

### 1. 放置 YOLO 權重

將訓練好的權重檔放至：

```
model/v1.0/best.pt
```

### 2. 建立 `.env`

請先複製 `.env.example` 作為 `.env` 設定檔，並依實際執行環境填入所需參數。

### 3. 啟動服務

```bash
# 啟動 DB 與 FastAPI
docker compose up --build -d db api

# 執行一次 Go batch（處理完後容器自動結束）
docker compose run --rm batch
```

---

## 環境變數說明

| 變數名稱 | 用途 | 預設值 |
|---|---|---|
| `APP_PORT` | FastAPI 對外服務 port | `8000` |
| `MODEL_VERSION` | YOLO 權重版本，對應路徑 `model/<version>/` | `v1.0` |
| `MODEL_WEIGHT_NAME` | YOLO 權重檔名 | `best.pt` |
| `YOLO_CONF` | YOLO 偵測信心值門檻 | `0.5` |
| `POSTGRES_DB` | PostgreSQL 資料庫名稱 | `invoice` |
| `POSTGRES_USER` | PostgreSQL 帳號 | `postgres` |
| `POSTGRES_PASSWORD` | PostgreSQL 密碼 | *(請自行設定)* |
| `POSTGRES_EXTERNAL_PORT` | PostgreSQL 對外曝露 port（host 端存取） | `5432` |
| `BATCH_INPUT_DIR` | Go batch 掃描的 host 端圖片目錄 | `./test_samples` |
| `BATCH_WORKERS` | Go batch 並行 worker 數 | `2` |

---

## API 說明

### `GET /health`

服務健康檢查，Docker compose healthcheck 使用。

```json
{ "status": "ok" }
```

---

### `POST /analyze`

上傳單張發票圖片，執行 YOLO + OCR 辨識並完成對獎，回傳 JSON 結果。

- **輸入**：`multipart/form-data`，欄位名稱為 `file`，支援 `jpg` / `png`
- **DB 寫入**：FastAPI 不寫入 DB，結果由 Go batch 服務負責寫入

**回傳 JSON 範例（未中獎）：**

```json
{
  "success": true,
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "image_name": "IMG_0001.jpg",
    "model_version": "v1.0",
    "invoice": {
      "invoice_number": "AB-12345678",
      "invoice_date": "2026-01-15",
      "invoice_time": "13:45:00",
      "total_amount": 100,
      "merchant_name": "範例商家"
    },
    "yolo_confidence": {
      "invoice_number": 0.96,
      "invoice_date": 0.91,
      "invoice_time": 0.88,
      "total_amount": 0.94,
      "merchant_name": 0.83
    },
    "award_result": {
      "period": "115年01-02月",
      "is_winning": false,
      "prize_type": null,
      "prize_amount": 0,
      "matched_number": null
    }
  }
}
```

---

## Database 說明

資料庫使用 PostgreSQL，共四張表，於容器首次啟動時透過 `init.sql` 自動建立與載入初始對獎號碼。

| Table 名稱 | 儲存內容 | 主要用途 |
|---|---|---|
| `invoice_awards` | 台灣統一發票各期對獎號碼（特別獎、特獎、頭獎等） | 對獎比對基準資料 |
| `invoice_inference_results` | OCR 辨識結果與對獎結果，含 `request_id` 追蹤 | 儲存每張發票的推論與中獎資訊 |
| `invoice_inference_conf` | YOLO 各欄位偵測信心值（0~1） | 作為模型效能監控與潛在 Model Drift 的觀察依據 |
| `invoice_batch_inference_logs` | Go batch 每張圖片的處理狀態、耗時與錯誤訊息 | 批次執行紀錄與失敗排查 |

`request_id`（UUID）可跨表追蹤同一次推論的完整記錄。

---

## Power BI 視覺化呈現

專案提供 Power BI 儀錶板，將發票辨識與對獎結果轉換為互動式視覺化分析，包含總消費金額、總中獎金額、中獎發票明細、每月消費趨勢與消費類別分布，並支援依期別、日期範圍與消費種類進行篩選。

* [下載 Power BI 儀錶板](https://drive.google.com/file/d/1zpPUsnKZqYLohfRG4ZiC9lNWLI5LDrRf/view?usp=sharing)

---

## 專案介紹投影片

專案提供完整介紹投影片，內容包含專案背景、系統流程、YOLO 與 OCR 辨識方式、FastAPI + Go Batch 系統架構、工程化設計亮點，以及 Power BI 視覺化成果。

* [下載專案介紹投影片](https://drive.google.com/file/d/14-8PSAVZ4NjjNjscMVqV8MjI2ZvVyROe/view?usp=sharing)
