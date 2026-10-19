# syntax=docker/dockerfile:1
#
# FinAgent 镜像。
# 镜像约 743MB（实测）：akshare + pandas 本身就不小，这是已知代价。
# 想瘦身可以拆成"只装问答所需依赖"的变体，但那样入库功能就没了。
#
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖 —— requirements 不变就不会重装（利用层缓存）
COPY requirements.txt .
RUN pip install -r requirements.txt

# 再拷代码与语料
COPY backend/ backend/
COPY finagent/ finagent/
COPY apps/ apps/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY data/raw/ data/raw/
COPY .env.example README.md pyproject.toml ./

EXPOSE 8000

# 健康检查用 python 自带库，省掉装 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
