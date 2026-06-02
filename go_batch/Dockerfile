# ---- Stage 1: Build ----
FROM golang:1.23-alpine AS builder

WORKDIR /build

# 複製 go.mod 與原始碼，再執行 go mod tidy 產生 go.sum 並下載依賴
COPY go.mod ./
COPY main.go ./
RUN go mod tidy && CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o batch-runner .

# ---- Stage 2: Runtime ----
FROM alpine:3.20

# 時區支援與 HTTPS 憑證
RUN apk add --no-cache tzdata ca-certificates

ENV TZ=Asia/Taipei

WORKDIR /app
COPY --from=builder /build/batch-runner .

ENTRYPOINT ["/app/batch-runner"]
