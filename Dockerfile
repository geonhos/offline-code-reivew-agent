FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성 (psycopg binary용)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# 의존성 설치
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 소스 코드 복사
COPY src/ src/
COPY scripts/ scripts/

# 포트 노출
EXPOSE 8000

# 서버 실행
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
