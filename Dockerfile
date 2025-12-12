FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 設定環境變數，確保 Log 會顯示
ENV PYTHONUNBUFFERED=1

# 使用 Gunicorn 啟動
CMD ["python", "-m", "gunicorn", "app:app", "--workers", "1", "--threads", "8", "--timeout", "0", "--bind", "0.0.0.0:8080"]