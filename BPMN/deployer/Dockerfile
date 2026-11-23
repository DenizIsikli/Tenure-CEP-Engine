FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    DATABASE_PATH=/data/professor_transfers.sqlite3

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py transfer_rules.py ./
RUN mkdir -p /data
EXPOSE 5001
CMD ["python", "app.py"]
