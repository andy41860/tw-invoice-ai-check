# ---- Base image：Python 3.11 slim ----
FROM python:3.11-slim

# 設定時區為台北（Taipei UTC+8）
ENV TZ=Asia/Taipei
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# ---- 安裝系統依賴 ----
# libgl1 + libglib2.0-0：OpenCV 與 EasyOCR 執行時期必要的 shared library
# libgomp1：ultralytics/PyTorch CPU 執行需要 OpenMP
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ---- 安裝 uv ----
RUN pip install --no-cache-dir uv

# ---- 工作目錄 ----
WORKDIR /app

# ---- 安裝 Python 依賴（利用 Docker layer cache：先複製 pyproject.toml）----
# 用 tomllib（Python 3.11 標準函式庫）從 pyproject.toml 讀出依賴清單寫入暫存檔，
# 再讓 uv 安裝，避免 -e . 需要完整套件結構的問題。
COPY pyproject.toml ./
RUN python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); open('/tmp/req.txt','w').write('\n'.join(d['project']['dependencies']))" \
    && uv pip install --system --no-cache -r /tmp/req.txt

# ---- 複製應用程式程式碼 ----
COPY app/ ./app/

# ---- model/ 目錄（掛載或 COPY 皆可；建議在 docker-compose.yml 使用 volume 掛載）----
# 注意：若直接 COPY，修改權重需重新 build image。
# 建議方式：docker-compose.yml 的 volumes: ./model:/app/model
# 若需要在 Dockerfile 內 COPY（不用 volume）請取消下行註解：
# COPY model/ ./model/

# ---- 預設啟動指令（host/port 由環境變數控制）----
CMD ["sh", "-c", "uvicorn app.main:app --host ${APP_HOST:-0.0.0.0} --port ${APP_PORT:-8000}"]
