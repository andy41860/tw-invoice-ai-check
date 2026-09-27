// -*- coding: utf-8 -*-
// Go batch 批次推論服務：掃描圖片資料夾 → 呼叫 FastAPI /analyze → 寫入 PostgreSQL。
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

// =========================================================
// API Response Structs
// =========================================================

// ApiResponse 對應 FastAPI /analyze 最外層回傳
type ApiResponse struct {
	Success   bool     `json:"success"`
	RequestID string   `json:"request_id"`
	Data      *ApiData `json:"data"`
	Error     string   `json:"error"`
}

// ApiData 對應 success=true 時的 data 欄位
type ApiData struct {
	ImageName    string         `json:"image_name"`
	ModelVersion string         `json:"model_version"`
	Invoice      InvoiceData    `json:"invoice"`
	YoloConf     YoloConfidence `json:"yolo_confidence"`
	AwardResult  AwardResult    `json:"award_result"`
}

// InvoiceData 對應 OCR 解析的發票欄位
type InvoiceData struct {
	InvoiceNumber *string `json:"invoice_number"`
	InvoiceDate   *string `json:"invoice_date"`
	InvoiceTime   *string `json:"invoice_time"`
	TotalAmount   *int    `json:"total_amount"`
	MerchantName  *string `json:"merchant_name"`
}

// YoloConfidence 對應各欄位 YOLO 偵測信心值（未偵測到時為 null）
type YoloConfidence struct {
	InvoiceNumber *float64 `json:"invoice_number"`
	InvoiceDate   *float64 `json:"invoice_date"`
	InvoiceTime   *float64 `json:"invoice_time"`
	TotalAmount   *float64 `json:"total_amount"`
	MerchantName  *float64 `json:"merchant_name"`
}

// AwardResult 對應對獎結果
type AwardResult struct {
	Period        *string `json:"period"`
	IsWinning     bool    `json:"is_winning"`
	PrizeType     *string `json:"prize_type"`
	PrizeAmount   int     `json:"prize_amount"`
	MatchedNumber *string `json:"matched_number"`
}

// =========================================================
// Config
// =========================================================

// Config 儲存從環境變數讀取的設定
type Config struct {
	APIURL      string
	InputDir    string
	Workers     int
	DatabaseURL string
}

// LoadConfig 從環境變數讀取設定，未設定者使用預設值
func LoadConfig() Config {
	workers := 2
	if w := os.Getenv("WORKERS"); w != "" {
		if parsed, err := strconv.Atoi(w); err == nil && parsed > 0 {
			workers = parsed
		}
	}
	return Config{
		APIURL:      getEnvOrDefault("API_URL", "http://api:8000"),
		InputDir:    getEnvOrDefault("INPUT_DIR", "/data/test_samples"),
		Workers:     workers,
		DatabaseURL: getEnvOrDefault("DATABASE_URL", "postgres://postgres:postgres@db:5432/invoice?sslmode=disable"),
	}
}

// getEnvOrDefault 讀取環境變數，若未設定則回傳 defaultVal
func getEnvOrDefault(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

// =========================================================
// Worker Pool Types
// =========================================================

// ImageTask 是送入 worker pool 的工作單元
type ImageTask struct {
	FilePath string
	FileName string
}

// ImageResult 是 worker 處理後的結果
type ImageResult struct {
	FileName     string
	Success      bool
	RequestID    string
	InferenceMs  int64
	ErrorMessage string
}

// =========================================================
// DB 連線
// =========================================================

// ConnectDB 建立 pgxpool 連線池，並重試 Ping 最多 maxRetry 次
func ConnectDB(ctx context.Context, databaseURL string) (*pgxpool.Pool, error) {
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, fmt.Errorf("建立連線池失敗：%w", err)
	}

	maxRetry := 5
	for i := 1; i <= maxRetry; i++ {
		if err := pool.Ping(ctx); err == nil {
			return pool, nil
		}
		log.Printf("[DB] Ping 失敗（第 %d/%d 次），2 秒後重試...", i, maxRetry)
		time.Sleep(2 * time.Second)
	}
	pool.Close()
	return nil, fmt.Errorf("資料庫連線逾時，已重試 %d 次", maxRetry)
}

// =========================================================
// 圖片掃描
// =========================================================

// ScanImages 掃描指定目錄，回傳所有 .jpg/.jpeg/.png 的絕對路徑
func ScanImages(inputDir string) ([]string, error) {
	var images []string
	entries, err := os.ReadDir(inputDir)
	if err != nil {
		return nil, fmt.Errorf("無法讀取目錄 %s：%w", inputDir, err)
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		ext := strings.ToLower(filepath.Ext(entry.Name()))
		if ext == ".jpg" || ext == ".jpeg" || ext == ".png" {
			images = append(images, filepath.Join(inputDir, entry.Name()))
		}
	}
	return images, nil
}

// =========================================================
// API 呼叫
// =========================================================

// CallAnalyzeAPI 以 multipart/form-data 呼叫 FastAPI /api/v1/analyze，回傳解析後的 ApiResponse 與耗時
func CallAnalyzeAPI(apiURL, filePath, fileName string) (*ApiResponse, int64, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return nil, 0, fmt.Errorf("無法開啟檔案 %s：%w", filePath, err)
	}
	defer f.Close()

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	// 欄位名稱必須是 "file"，與 FastAPI 端點定義一致
	part, err := writer.CreateFormFile("file", fileName)
	if err != nil {
		return nil, 0, fmt.Errorf("建立 multipart form 失敗：%w", err)
	}
	if _, err = io.Copy(part, f); err != nil {
		return nil, 0, fmt.Errorf("寫入 multipart body 失敗：%w", err)
	}
	writer.Close()

	startTime := time.Now()
	resp, err := http.Post(apiURL+"/api/v1/analyze", writer.FormDataContentType(), &body)
	inferenceMs := time.Since(startTime).Milliseconds()

	if err != nil {
		return nil, inferenceMs, fmt.Errorf("HTTP 請求失敗：%w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		rawBody, _ := io.ReadAll(resp.Body)
		return nil, inferenceMs, fmt.Errorf("HTTP %d：%s", resp.StatusCode, string(rawBody))
	}

	var apiResp ApiResponse
	if err := json.NewDecoder(resp.Body).Decode(&apiResp); err != nil {
		return nil, inferenceMs, fmt.Errorf("JSON 解析失敗：%w", err)
	}

	return &apiResp, inferenceMs, nil
}

// =========================================================
// 型別轉換輔助函數
// =========================================================

// ParseUUID 將 UUID 字串轉為 pgtype.UUID
func ParseUUID(s string) (pgtype.UUID, error) {
	var u pgtype.UUID
	if err := u.Scan(s); err != nil {
		return pgtype.UUID{}, fmt.Errorf("UUID 解析失敗 %q：%w", s, err)
	}
	return u, nil
}

// ParseDate 將 "YYYY-MM-DD" 字串轉為 pgtype.Date；空值或 nil 回傳 Invalid
func ParseDate(s *string) pgtype.Date {
	if s == nil || *s == "" {
		return pgtype.Date{Valid: false}
	}
	t, err := time.Parse("2006-01-02", *s)
	if err != nil {
		return pgtype.Date{Valid: false}
	}
	return pgtype.Date{Time: t, Valid: true}
}

// ParseTime 將 "HH:MM:SS" 字串轉為 pgtype.Time；空值或 nil 回傳 Invalid
func ParseTime(s *string) pgtype.Time {
	if s == nil || *s == "" {
		return pgtype.Time{Valid: false}
	}
	t, err := time.Parse("15:04:05", *s)
	if err != nil {
		return pgtype.Time{Valid: false}
	}
	// pgtype.Time.Microseconds 是從 00:00:00.000000 起算的微秒數
	micros := int64(t.Hour())*3_600_000_000 +
		int64(t.Minute())*60_000_000 +
		int64(t.Second())*1_000_000
	return pgtype.Time{Microseconds: micros, Valid: true}
}

// ParseConfidence 將 *float64 轉為 pgtype.Numeric；nil 回傳 Invalid
func ParseConfidence(f *float64) pgtype.Numeric {
	if f == nil {
		return pgtype.Numeric{Valid: false}
	}
	var n pgtype.Numeric
	// 用字串格式避免浮點精度問題，NUMERIC(6,4) 最多 4 位小數
	_ = n.Scan(fmt.Sprintf("%.4f", *f))
	return n
}

// =========================================================
// DB 寫入函數
// =========================================================

// InsertInferenceResult 寫入 invoice_inference_results，回傳新增記錄的 id
func InsertInferenceResult(
	ctx context.Context,
	pool *pgxpool.Pool,
	requestUUID pgtype.UUID,
	data *ApiData,
) (int64, error) {
	sql := `
		INSERT INTO invoice_inference_results (
			request_id, image_name, model_version,
			invoice_number, invoice_date, invoice_time,
			total_amount, merchant_name,
			award_period, is_winning, prize_type, prize_amount, matched_number
		) VALUES (
			$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
		)
		RETURNING id`

	var id int64
	err := pool.QueryRow(ctx, sql,
		requestUUID,
		data.ImageName,
		data.ModelVersion,
		data.Invoice.InvoiceNumber,
		ParseDate(data.Invoice.InvoiceDate),
		ParseTime(data.Invoice.InvoiceTime),
		data.Invoice.TotalAmount,
		data.Invoice.MerchantName,
		data.AwardResult.Period,
		data.AwardResult.IsWinning,
		data.AwardResult.PrizeType,
		data.AwardResult.PrizeAmount,
		data.AwardResult.MatchedNumber,
	).Scan(&id)
	if err != nil {
		return 0, fmt.Errorf("insert inference result：%w", err)
	}
	return id, nil
}

// InsertInferenceConf 批次寫入 invoice_inference_conf（5 個欄位的 confidence）
func InsertInferenceConf(
	ctx context.Context,
	pool *pgxpool.Pool,
	requestUUID pgtype.UUID,
	inferenceResultID int64,
	imageName string,
	conf YoloConfidence,
) error {
	sql := `
		INSERT INTO invoice_inference_conf
			(request_id, inference_result_id, image_name, field_name, confidence)
		VALUES ($1, $2, $3, $4, $5)
		ON CONFLICT (inference_result_id, field_name) DO NOTHING`

	// 使用 pgx.Batch 一次送出 5 筆，減少 round-trip
	fields := []struct {
		name string
		val  *float64
	}{
		{"invoice_number", conf.InvoiceNumber},
		{"invoice_date", conf.InvoiceDate},
		{"invoice_time", conf.InvoiceTime},
		{"total_amount", conf.TotalAmount},
		{"merchant_name", conf.MerchantName},
	}

	batch := &pgx.Batch{}
	for _, f := range fields {
		batch.Queue(sql, requestUUID, inferenceResultID, imageName, f.name, ParseConfidence(f.val))
	}

	br := pool.SendBatch(ctx, batch)
	defer br.Close()

	for range fields {
		if _, err := br.Exec(); err != nil {
			return fmt.Errorf("insert inference conf：%w", err)
		}
	}
	return nil
}

// InsertBatchLog 寫入 invoice_batch_inference_logs
func InsertBatchLog(
	ctx context.Context,
	pool *pgxpool.Pool,
	requestID *string,
	imageName, imagePath, status string,
	errorMsg *string,
	inferenceMs int64,
	modelVersion *string,
) error {
	// request_id 欄位允許 NULL（失敗時可能沒有取得 request_id）
	var requestUUID interface{} = nil
	if requestID != nil && *requestID != "" {
		u, err := ParseUUID(*requestID)
		if err == nil {
			requestUUID = u
		}
	}

	sql := `
		INSERT INTO invoice_batch_inference_logs
			(request_id, image_name, image_path, status, error_message, inference_time_ms, model_version)
		VALUES ($1, $2, $3, $4, $5, $6, $7)`

	_, err := pool.Exec(ctx, sql,
		requestUUID, imageName, imagePath, status, errorMsg, inferenceMs, modelVersion)
	if err != nil {
		return fmt.Errorf("insert batch log：%w", err)
	}
	return nil
}

// =========================================================
// 單張圖片的完整處理流程
// =========================================================

// WriteSuccessDB 成功時寫入三張表：inference_results → inference_conf → batch_log
func WriteSuccessDB(ctx context.Context, pool *pgxpool.Pool, resp *ApiResponse, imagePath string, inferenceMs int64) error {
	requestUUID, err := ParseUUID(resp.RequestID)
	if err != nil {
		return fmt.Errorf("UUID 解析失敗：%w", err)
	}

	resultID, err := InsertInferenceResult(ctx, pool, requestUUID, resp.Data)
	if err != nil {
		return err
	}

	if err := InsertInferenceConf(ctx, pool, requestUUID, resultID, resp.Data.ImageName, resp.Data.YoloConf); err != nil {
		return err
	}

	mv := resp.Data.ModelVersion
	return InsertBatchLog(ctx, pool, &resp.RequestID, resp.Data.ImageName, imagePath, "success", nil, inferenceMs, &mv)
}

// WriteFailDB 失敗時只寫入 batch_log（status='failed'）
func WriteFailDB(ctx context.Context, pool *pgxpool.Pool, requestID *string, imageName, imagePath, errMsg string, inferenceMs int64) {
	if err := InsertBatchLog(ctx, pool, requestID, imageName, imagePath, "failed", &errMsg, inferenceMs, nil); err != nil {
		log.Printf("[WARN] 寫入 batch log 失敗（%s）：%v", imageName, err)
	}
}

// ProcessImage 處理單張圖片：呼叫 API → 寫入 DB → 回傳結果
func ProcessImage(cfg Config, pool *pgxpool.Pool, task ImageTask) ImageResult {
	result := ImageResult{FileName: task.FileName}
	ctx := context.Background()

	apiResp, inferenceMs, err := CallAnalyzeAPI(cfg.APIURL, task.FilePath, task.FileName)
	result.InferenceMs = inferenceMs

	if err != nil {
		errMsg := err.Error()
		log.Printf("[FAIL] %s：API 呼叫失敗 - %s", task.FileName, errMsg)
		WriteFailDB(ctx, pool, nil, task.FileName, task.FilePath, errMsg, inferenceMs)
		result.ErrorMessage = errMsg
		return result
	}

	if !apiResp.Success {
		errMsg := apiResp.Error
		if errMsg == "" {
			errMsg = "API 回傳 success=false"
		}
		log.Printf("[FAIL] %s：%s", task.FileName, errMsg)
		WriteFailDB(ctx, pool, &apiResp.RequestID, task.FileName, task.FilePath, errMsg, inferenceMs)
		result.ErrorMessage = errMsg
		return result
	}

	if err := WriteSuccessDB(ctx, pool, apiResp, task.FilePath, inferenceMs); err != nil {
		errMsg := fmt.Sprintf("DB 寫入失敗：%v", err)
		log.Printf("[FAIL] %s：%s", task.FileName, errMsg)
		WriteFailDB(ctx, pool, &apiResp.RequestID, task.FileName, task.FilePath, errMsg, inferenceMs)
		result.ErrorMessage = errMsg
		return result
	}

	log.Printf("[OK] %s（request_id=%s，耗時 %dms）", task.FileName, apiResp.RequestID, inferenceMs)
	result.Success = true
	result.RequestID = apiResp.RequestID
	return result
}

// =========================================================
// Worker Pool
// =========================================================

// RunBatch 啟動 worker pool，並行處理所有圖片，回傳所有結果
func RunBatch(cfg Config, pool *pgxpool.Pool, imagePaths []string) []ImageResult {
	taskCh := make(chan ImageTask, len(imagePaths))
	resultCh := make(chan ImageResult, len(imagePaths))

	var wg sync.WaitGroup
	for i := 0; i < cfg.Workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for task := range taskCh {
				resultCh <- ProcessImage(cfg, pool, task)
			}
		}()
	}

	for _, p := range imagePaths {
		taskCh <- ImageTask{
			FilePath: p,
			FileName: filepath.Base(p),
		}
	}
	close(taskCh)

	go func() {
		wg.Wait()
		close(resultCh)
	}()

	var results []ImageResult
	for r := range resultCh {
		results = append(results, r)
	}
	return results
}

// =========================================================
// 摘要輸出
// =========================================================

// PrintSummary 在 stdout 印出批次處理摘要
func PrintSummary(results []ImageResult, total int, startedAt time.Time, finishedAt time.Time) {
	successCount := 0
	failCount := 0
	for _, r := range results {
		if r.Success {
			successCount++
		} else {
			failCount++
		}
	}

	elapsedMs := finishedAt.Sub(startedAt).Milliseconds()

	fmt.Println("========================================")
	fmt.Printf("批次推論完成\n")
	fmt.Printf("total:       %d\n", total)
	fmt.Printf("success:     %d\n", successCount)
	fmt.Printf("failed:      %d\n", failCount)
	fmt.Printf("started_at:  %s\n", startedAt.Format("2006-01-02 15:04:05"))
	fmt.Printf("finished_at: %s\n", finishedAt.Format("2006-01-02 15:04:05"))
	fmt.Printf("elapsed_ms:  %d\n", elapsedMs)
	fmt.Println("========================================")

	if failCount > 0 {
		fmt.Println("失敗清單：")
		for _, r := range results {
			if !r.Success {
				fmt.Printf("  - %s：%s\n", r.FileName, r.ErrorMessage)
			}
		}
	}
}

// =========================================================
// main
// =========================================================

func main() {
	cfg := LoadConfig()
	log.Printf("[Config] API_URL=%s  INPUT_DIR=%s  WORKERS=%d", cfg.APIURL, cfg.InputDir, cfg.Workers)

	ctx := context.Background()
	pool, err := ConnectDB(ctx, cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("[Fatal] %v", err)
	}
	defer pool.Close()

	imagePaths, err := ScanImages(cfg.InputDir)
	if err != nil {
		log.Fatalf("[Fatal] %v", err)
	}
	if len(imagePaths) == 0 {
		log.Printf("[Info] %s 中沒有找到圖片（.jpg/.jpeg/.png），結束。", cfg.InputDir)
		return
	}
	log.Printf("[Info] 找到 %d 張圖片，開始批次推論（workers=%d）...", len(imagePaths), cfg.Workers)

	startedAt := time.Now()
	results := RunBatch(cfg, pool, imagePaths)
	finishedAt := time.Now()

	PrintSummary(results, len(imagePaths), startedAt, finishedAt)
}
