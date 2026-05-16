# 使用精简版 Python，减小镜像体积
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    redis-server supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
