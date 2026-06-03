FROM python:3.11-slim

WORKDIR /api

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# SQLite DB lives in a mounted volume
VOLUME ["/data"]
ENV DATABASE_URL=sqlite:////data/store_intelligence.db

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
