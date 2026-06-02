# 發票 OCR 對獎服務

台灣電子發票證明聯 YOLO+OCR 辨識 + 統一發票對獎，以 FastAPI 包裝，支援 Docker Compose 一鍵啟動。

---

## 專案結構

```
專案根目錄/
├── .env                      # 實際環境變數（不進 git）
├── .env.example              # 環境變數範本
├── .gitignore
├── .dockerignore
├── docker-compose.yml        # api + db + batch 三個服務
├── Dockerfile                # FastAPI：以 uv 安裝、跑 uvicorn
├── pyproject.toml            # uv 套件管理
├── batch_invoice_ocr.py      # 既有 OCR 核心（不可修改）
├── init.sql                  # 對獎號碼建表 + 初始資料 + batch log table
├── model/
│   └── v1.0/
│       └── best.pt           # YOLO 權重（需手動放置，不進 git）
├── go_batch/                 # Go batch 批次推論服務
│   ├── Dockerfile            # multi-stage Go build
│   ├── go.mod
│   └── main.go
└── app/
    ├── main.py               # FastAPI app + lifespan
    ├── config.py             # pydantic-settings 設定
    ├── schemas.py            # Pydantic 輸入/輸出模型
    ├── dependencies.py       # DI：取得 models / DB 連線
    ├── inference.py          # 封裝 YOLO+OCR（重用 batch_invoice_ocr.py）
    ├── db.py                 # PostgreSQL 連線與查詢
    ├── prize.py              # 對獎邏輯
    └── utils.py              # 共用工具函數
```

---

## 快速啟動

### 1. 放置 YOLO 權重

```
model/v1.0/best.pt
```

> 若使用其他版本，修改 `.env` 的 `MODEL_VERSION=v1.1`，並放置 `model/v1.1/best.pt`，**不需要改程式碼**。

### 2. 建立 `.env`

```bash
cp .env.example .env
# 視需要修改 POSTGRES_PASSWORD 等敏感資訊
```

### 3. 一鍵啟動

```bash
docker compose up --build -d
```

啟動後：
- API 服務：`http://localhost:8000`
- API 文件：`http://localhost:8000/docs`
- db 容器**首次啟動**時會自動執行 `init.sql`（建表 + 載入對獎號碼）

---

## 環境變數說明

| 變數 | 預設值 | 說明 |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | FastAPI 服務 host |
| `APP_PORT` | `8000` | FastAPI 服務 port |
| `MODEL_VERSION` | `v1.0` | YOLO 權重版本，對應 `model/<版本>/best.pt` |
| `MODEL_WEIGHT_NAME` | `best.pt` | 權重檔名 |
| `DEVICE` | `cpu` | 推論裝置（固定 cpu，不支援 GPU）|
| `POSTGRES_HOST` | `db` | DB host（docker compose 內用服務名）|
| `POSTGRES_PORT` | `5432` | 容器內部 port |
| `POSTGRES_DB` | `invoice` | 資料庫名稱 |
| `POSTGRES_USER` | `postgres` | 資料庫帳號 |
| `POSTGRES_PASSWORD` | `postgres` | 資料庫密碼 |
| `POSTGRES_EXTERNAL_PORT` | `5432` | 對外曝露的 DB port（host 端存取用）|

---

## API 使用說明

### 健康檢查

```bash
curl http://localhost:8000/health
```

回傳：
```json
{"status": "ok"}
```

### 發票 OCR + 對獎

```bash
curl -X POST http://localhost:8000/analyze \
  -F "file=@IMG_0001.jpg"
```

**回傳範例（未中獎）：**

```json
{
  "success": true,
  "data": {
    "image_name": "IMG_0001.jpg",
    "model_version": "v1.0",
    "invoice": {
      "invoice_number": "AB-12345678",
      "invoice_date": "2026-01-15",
      "invoice_time": "13:45:22",
      "total_amount": 120,
      "merchant_name": "全家便利商店"
    },
    "yolo_confidence": {
      "invoice_number": 0.9821,
      "invoice_date": 0.9543,
      "invoice_time": 0.9312,
      "total_amount": 0.9644,
      "merchant_name": null
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

## 對獎規則說明

依台灣財政部統一發票兌獎規則，各獎別比對位數：

| 獎別 | 比對方式 | 獎金 |
|---|---|---|
| 特別獎 | 後 8 碼完全相符 | 1,000 萬 |
| 特獎 | 後 8 碼完全相符 | 200 萬 |
| 頭獎 | 後 8 碼完全相符（3 組）| 20 萬 |
| 二獎 | 後 7 碼符合任一頭獎組 | 4 萬 |
| 三獎 | 後 6 碼符合任一頭獎組 | 1 萬 |
| 四獎 | 後 5 碼符合任一頭獎組 | 4,000 |
| 五獎 | 後 4 碼符合任一頭獎組 | 1,000 |
| 六獎 | 後 3 碼符合任一頭獎組 | 200 |
| 增開六獎 | 後 3 碼符合增開六獎號碼 | 200 |

---

## 注意事項

### init.sql 重新執行

`init.sql` 只在 **db volume 為空（首次初始化）時** 執行。

若修改了 `init.sql`（例如新增期別的對獎號碼），需先移除 volume 再重啟：

```bash
docker compose down -v
docker compose up --build -d
```

> ⚠️ `down -v` 會刪除所有 volume 資料，包含已存在的對獎記錄。

### 模型版本切換

修改 `.env` 的 `MODEL_VERSION`（如 `v1.1`），放置對應權重 `model/v1.1/best.pt`，重啟服務即可：

```bash
docker compose restart api
```

回傳的 `model_version` 欄位會自動對應。

---

## Go Batch 批次推論服務

`go_batch/` 是 Go 語言實作的批次推論服務，負責掃描指定目錄中的圖片，呼叫 FastAPI `/analyze` 推論後，將結果寫入 PostgreSQL。

### 服務職責分工

| 服務 | 職責 |
|---|---|
| FastAPI (`api`) | 單張圖片推論、OCR、對獎判斷，回傳 JSON |
| Go batch (`batch`) | 掃描資料夾、並行呼叫 FastAPI、寫入 DB |
| PostgreSQL (`db`) | 保存推論結果、confidence、批次 log |

### 使用方式

```bash
# 啟動 DB 與 FastAPI
docker compose up --build -d db api

# 執行一次 Go batch（處理完後容器自動結束）
docker compose run --rm batch

# 看 API log
docker compose logs -f api

# 查詢 batch 處理記錄
docker compose exec db psql -U postgres -d invoice -c "SELECT * FROM invoice_batch_inference_logs ORDER BY id DESC LIMIT 20;"

# 若 init.sql schema 有變更，需重建 DB volume
docker compose down -v
docker compose up --build -d db api
```

### Go Batch 環境變數

| 變數 | 預設值 | 說明 |
|---|---|---|
| `BATCH_API_URL` | `http://api:8000` | FastAPI 服務 URL（容器內使用服務名） |
| `BATCH_INPUT_DIR` | `./test_samples` | Host 端圖片目錄（掛載到容器） |
| `CONTAINER_BATCH_INPUT_DIR` | `/data/test_samples` | 容器內的掛載路徑 |
| `BATCH_WORKERS` | `2` | 並行 worker 數量 |

### DB 寫入說明

批次推論成功後，Go 服務會寫入三張表：

1. `invoice_inference_results`：OCR 辨識結果與對獎結果
2. `invoice_inference_conf`：各欄位 YOLO confidence
3. `invoice_batch_inference_logs`：處理日誌（成功或失敗均記錄）

失敗的圖片（API 錯誤、逾時、DB 寫入失敗等）只寫 `invoice_batch_inference_logs(status='failed')`，不影響其他圖片繼續執行。

---

## 本機開發（不用 Docker）

```bash
# 安裝依賴
uv pip install -e .

# 設定環境變數（DB 指向本機 PostgreSQL）
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
# ... 其他變數

# 啟動服務
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
