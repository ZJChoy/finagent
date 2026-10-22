# FinAgent 一键入口（Windows 下用 uvicorn/venv 或 WSL 均可；命令均为项目根目录相对）
.PHONY: help bootstrap serve test lint eval doctor clean

help:
	@echo "make bootstrap  首次部署：装依赖 → 入库语料 → 体检索引"
	@echo "make serve      起服务（127.0.0.1:8000）"
	@echo "make test       离线单测（不需要 API key）"
	@echo "make lint       ruff 检查"
	@echo "make eval       20 题评测（需服务在跑）"
	@echo "make doctor     索引体检（一致性 / stale 变体）"

bootstrap:
	pip install -r requirements.lock || uv pip install -r requirements.lock
	python scripts/ingest.py --dir data/raw --stats || true
	python scripts/ingest.py --dir data/raw
	python scripts/index_doctor.py

serve:
	python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

test:
	python -m pytest tests/ -q

lint:
	python -m ruff check backend finagent apps scripts tests

eval:
	python scripts/eval.py

doctor:
	python scripts/index_doctor.py

clean:
	python -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in (pathlib.Path('__pycache__'), pathlib.Path('.pytest_cache'))]"
