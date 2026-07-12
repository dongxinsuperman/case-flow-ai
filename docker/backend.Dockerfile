FROM python:3.11-slim

WORKDIR /app

COPY README.md /app/README.md
COPY backend /app/backend
RUN pip install --no-cache-dir -e /app/backend

WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]
