-- =========================================================
-- PostgreSQL init.sql
-- Purpose:
--   1. Create invoice_awards table
--   2. Create invoice_inference_results table
--   3. Create invoice_inference_conf table
--   4. Insert Taiwan Uniform Invoice award numbers
--      - ROC year 115, Jan-Feb
--      - ROC year 115, Mar-Apr
--
-- Notes:
--   - Award numbers must use TEXT, not INT.
--   - Some invoice numbers may start with 0, e.g. 07225810.
--   - This file is intended for:
--       /docker-entrypoint-initdb.d/init.sql
-- =========================================================


-- =========================================================
-- 1. Create table: invoice_awards
-- =========================================================

CREATE TABLE IF NOT EXISTS invoice_awards (
    id SERIAL PRIMARY KEY,

    -- 民國年，例如 115
    roc_year INTEGER NOT NULL,

    -- 期別起始月份，例如 1, 3, 5, 7, 9, 11
    period_month_start INTEGER NOT NULL,

    -- 期別結束月份，例如 2, 4, 6, 8, 10, 12
    period_month_end INTEGER NOT NULL,

    -- 顯示用期別，例如 115年01-02月
    period_label TEXT NOT NULL,

    -- 特別獎：1000萬
    special_prize TEXT NOT NULL,

    -- 特獎：200萬
    grand_prize TEXT NOT NULL,

    -- 頭獎：20萬，通常 3 組
    first_prizes TEXT[] NOT NULL,

    -- 增開六獎：200 元，可能沒有
    additional_sixth_prizes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],

    -- 領獎期間
    claim_start_date DATE,
    claim_end_date DATE,

    -- 資料來源
    source TEXT,

    -- 建立時間
    insert_time TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'Asia/Taipei'),

    -- 同一年同一期只能有一筆
    CONSTRAINT uq_invoice_awards_period UNIQUE (
        roc_year,
        period_month_start,
        period_month_end
    ),

    -- 基本檢查
    CONSTRAINT chk_period_month_start CHECK (
        period_month_start IN (1, 3, 5, 7, 9, 11)
    ),

    CONSTRAINT chk_period_month_end CHECK (
        period_month_end IN (2, 4, 6, 8, 10, 12)
    ),

    CONSTRAINT chk_period_order CHECK (
        period_month_end = period_month_start + 1
    ),

    CONSTRAINT chk_special_prize_format CHECK (
        special_prize ~ '^[0-9]{8}$'
    ),

    CONSTRAINT chk_grand_prize_format CHECK (
        grand_prize ~ '^[0-9]{8}$'
    )
);


-- =========================================================
-- 2. Create table: invoice_inference_results
--    Store invoice OCR result and award matching result
-- =========================================================

CREATE TABLE IF NOT EXISTS invoice_inference_results (
    id SERIAL PRIMARY KEY,

    -- 影像與模型資訊
    image_name TEXT NOT NULL,
    model_version TEXT,

    -- 發票 OCR 結果
    invoice_number TEXT,
    invoice_date DATE,
    invoice_time TIME,
    total_amount INTEGER,
    merchant_name TEXT,

    -- 對獎結果
    award_period TEXT,
    is_winning BOOLEAN NOT NULL DEFAULT FALSE,
    prize_type TEXT,
    prize_amount INTEGER NOT NULL DEFAULT 0,
    matched_number TEXT,

    -- 寫入時間
    insert_time TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'Asia/Taipei'),

    -- 基本檢查
    CONSTRAINT chk_invoice_number_format CHECK (
        invoice_number IS NULL OR invoice_number ~ '^[A-Z]{2}-?[0-9]{8}$'
    ),

    CONSTRAINT chk_total_amount_non_negative CHECK (
        total_amount IS NULL OR total_amount >= 0
    ),

    CONSTRAINT chk_prize_amount_non_negative CHECK (
        prize_amount >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_invoice_inference_results_invoice_number
ON invoice_inference_results (invoice_number);

CREATE INDEX IF NOT EXISTS idx_invoice_inference_results_invoice_date
ON invoice_inference_results (invoice_date);

CREATE INDEX IF NOT EXISTS idx_invoice_inference_results_insert_time
ON invoice_inference_results (insert_time);


-- =========================================================
-- 3. Create table: invoice_inference_conf
--    Store YOLO confidence values for each inferred field
-- =========================================================

CREATE TABLE IF NOT EXISTS invoice_inference_conf (
    id SERIAL PRIMARY KEY,

    -- 對應到推論結果
    inference_result_id INTEGER NOT NULL REFERENCES invoice_inference_results(id) ON DELETE CASCADE,

    -- 影像檔名
    image_name TEXT NOT NULL,

    -- 發票號碼
    invoice_number TEXT,

    -- 欄位名稱，例如 invoice_number, invoice_date, invoice_time, total_amount, merchant_name
    field_name TEXT NOT NULL,

    -- YOLO confidence，可為 NULL，例如 merchant_name 沒有用 YOLO 偵測
    confidence NUMERIC(6, 4),

    -- 寫入時間
    insert_time TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'Asia/Taipei'),

    -- 同一筆推論結果，同一欄位只存一筆
    CONSTRAINT uq_inference_confidence_field UNIQUE (
        inference_result_id,
        field_name
    ),

    -- confidence 範圍檢查
    CONSTRAINT chk_confidence_range CHECK (
        confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
    )
);

CREATE INDEX IF NOT EXISTS idx_invoice_inference_conf_result_id
ON invoice_inference_conf (inference_result_id);

CREATE INDEX IF NOT EXISTS idx_invoice_inference_conf_field_name
ON invoice_inference_conf (field_name);


-- =========================================================
-- 4. Insert award numbers: 115年01-02月
-- =========================================================

INSERT INTO invoice_awards (
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
)
VALUES (
    115,
    1,
    2,
    '115年01-02月',
    '87510041',
    '32220522',
    ARRAY[
        '21677046',
        '44662410',
        '31262513'
    ]::TEXT[],
    ARRAY[]::TEXT[],
    '2026-04-06',
    '2026-07-06',
    '財政部稅務入口網 / 財政部賦稅署'
)
ON CONFLICT (roc_year, period_month_start, period_month_end)
DO UPDATE SET
    period_label = EXCLUDED.period_label,
    special_prize = EXCLUDED.special_prize,
    grand_prize = EXCLUDED.grand_prize,
    first_prizes = EXCLUDED.first_prizes,
    additional_sixth_prizes = EXCLUDED.additional_sixth_prizes,
    claim_start_date = EXCLUDED.claim_start_date,
    claim_end_date = EXCLUDED.claim_end_date,
    source = EXCLUDED.source;


-- =========================================================
-- 5. Insert award numbers: 115年03-04月
-- =========================================================

INSERT INTO invoice_awards (
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
)
VALUES (
    115,
    3,
    4,
    '115年03-04月',
    '19531471',
    '85941329',
    ARRAY[
        '07225810',
        '20231230',
        '83518781'
    ]::TEXT[],
    ARRAY[]::TEXT[],
    '2026-06-06',
    '2026-09-07',
    '財政部稅務入口網 / 財政部賦稅署'
)
ON CONFLICT (roc_year, period_month_start, period_month_end)
DO UPDATE SET
    period_label = EXCLUDED.period_label,
    special_prize = EXCLUDED.special_prize,
    grand_prize = EXCLUDED.grand_prize,
    first_prizes = EXCLUDED.first_prizes,
    additional_sixth_prizes = EXCLUDED.additional_sixth_prizes,
    claim_start_date = EXCLUDED.claim_start_date,
    claim_end_date = EXCLUDED.claim_end_date,
    source = EXCLUDED.source;


