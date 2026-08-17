FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org

COPY app.py .
COPY modules/ ./modules/
COPY templates/ ./templates/
COPY static/ ./static/

RUN mkdir -p /data

EXPOSE 21114

ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]