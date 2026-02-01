# 使用精简版 Python，减小镜像体积
FROM python:3.11-slim

# 容器内工作目录
WORKDIR /app

# 先拷贝依赖清单，利用 Docker 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷贝全部源码
COPY . /app

# 默认启动 backend（TG 监听 + Web API）
CMD ["python", "app.py"]
