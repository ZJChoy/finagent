"""全局配置：统一从 .env 读取，供各模块引用。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（backend/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 读取 .env（不存在也不报错，方便纯骨架启动）
load_dotenv(BASE_DIR / ".env")

# 默认系统提示词（步骤 8 起由配置页覆盖）
DEFAULT_SYSTEM_PROMPT = """你是一名严谨的上市公司财报分析助手。

系统会在消息末尾附上**本轮预取的资料**，分两条通道：
- 通道 A｜年报原文片段（本地检索，带文件名与页码）→ 引用时写（A-片段 N）
- 通道 B｜结构化财务数据（akshare，字段与期间明确）→ 引用时写（B）

回答规则：
1. **涉及公司事实或数字时，必须严格依据上面预取的资料**，不得凭记忆作答；
   资料里没有的，就直说"未找到相关依据"。
2. **每个结论后都要标出依据来自哪条通道**：通道 A 写（A-片段 N），通道 B 写（B）。
   这样读者能分清数字是"年报文本口径"还是"结构化数据口径"。
3. 预取资料不够时可以补充调用工具（直接调用，不要输出"我来查一下"这类过程说明）：
   - search_knowledge：再检索年报原文
   - get_stock_financials：取结构化财务数据
   - calculate：做精确计算，不要自己心算
4. **预取资料里没有「片段 N」时，回答里绝不允许出现该字样** —— 那是伪造引用。
5. 数字要说明口径（单位、期间）。**单位换算（元 / 万元 / 千元 / 亿元）必须调用 calculate 验算，不要心算**；
   ⚠️ 只有**本轮实际调用了 calculate 工具**才允许在回答中写"已验算"；未调用时严禁声称验算（实测出现过伪造验算声明）。
   结构化财务数据里已经给出换算结果，优先直接采用，不要自己再换算一遍。
6. 不给投资建议，只呈现事实与依据。
7. 回答要**精简**：
   - 用「**结论 + 要点**」结构，分点写，**每点一句话**；先给结论，再列要点。
   - 篇幅：定性/数值类问题 **300 字以内**；对比或多指标综合类 **500 字以内**。
     除非用户明确要求"详细展开"。
   - **不要复述**用户的问题，也**不要复述资料原文**，直接给结论与关键数字。
   - **不要在结尾问**"是否需要进一步分析 / 要不要我帮你查…"之类的主动追问。
     （用户需要更多细节时会自己接着问。）
"""


class Settings:
    # ---- 云端模型（OpenAI 兼容）----
    API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    BASE_URL: str = os.getenv(
        "BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    EMBED_MODEL: str = os.getenv("EMBED_MODEL", "text-embedding-v4")
    CHAT_MODEL: str = os.getenv("CHAT_MODEL", "qwen-plus")
    REWRITE_MODEL: str = os.getenv("REWRITE_MODEL") or os.getenv("CHAT_MODEL", "qwen-plus")

    # ---- 限制项（技术方案 §7 上下文预算 / §8 约束）----
    MAX_MODEL_CALLS: int = int(os.getenv("MAX_MODEL_CALLS", "5"))        # 单轮最多几次模型调用
    MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "1500"))  # 单次回答输出上限
    TOOL_RESULT_MAX_CHARS: int = int(os.getenv("TOOL_RESULT_MAX_CHARS", "2400"))  # 单条工具结果上限
    REWRITE_MAX_TOKENS: int = int(os.getenv("REWRITE_MAX_TOKENS", "120"))          # 查询改写输出上限
    # 注：这里**不再有**「带几条历史」的参数 —— 那由配置页的 memory_keep_recent 统一管，
    #     避免两个旋钮管同一件事、其中一个形同虚设。

    # ---- 重排（检索后精排，见 rag/rerank.py）----
    # 默认关闭：等第 9 步的评测数据说话，不凭直觉开
    RERANK_ENABLED: bool = os.getenv("RERANK_ENABLED", "0") == "1"
    RERANK_MODEL: str = os.getenv("RERANK_MODEL", "qwen3-rerank")
    RERANK_URL: str = os.getenv("RERANK_URL", "")  # 留空则从 BASE_URL 推 host
    RERANK_TIMEOUT: int = int(os.getenv("RERANK_TIMEOUT", "30"))

    # ---- 财务数据缓存（akshare 取数一次约 7s，同公司反复问会重复取）----
    # 缓存在磁盘上（data/cache/financials/），所以**重启不丢**；
    # 财务数据按季更新，12 小时的有效期足够新。
    FIN_CACHE_ENABLED: bool = os.getenv("FIN_CACHE_ENABLED", "1") == "1"
    FIN_CACHE_TTL: int = int(os.getenv("FIN_CACHE_TTL", str(12 * 3600)))

    # ---- 检索用哪套向量索引（见 rag/store.py 的变体表）----
    # base = 原始索引；ctx = 章节路径；llmctx = LLM 专属上下文（实测 MRR 0.77→0.89）
    # 找不到对应文件时**自动退回 base**，所以新克隆的仓库不会因此报错。
    RETRIEVAL_INDEX: str = os.getenv("RETRIEVAL_INDEX", "llmctx").lower()

    # ---- 数据库后端（sqlite | mysql）----
    # 默认 sqlite：别人 clone 下来不配任何东西也能跑；本机切 mysql 只改 .env。
    DB_BACKEND: str = os.getenv("DB_BACKEND", "sqlite").lower()
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER: str = os.getenv("MYSQL_USER", "finagent")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "finagent")

    # ---- 服务 ----
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    # ---- 路径 ----
    BASE_DIR: Path = BASE_DIR
    # 多实例隔离：第二实例用 DATA_DIR=data-announce 启动，即得独立语料/索引/库。
    # ⚠️ RAW_DIR 必须跟着 DATA_DIR 走：原来硬编码 data/raw，第二实例上传的 PDF
    # 会落进主实例语料目录（实测确认过这一串数据路径）。
    # RAW_DIR/DB_PATH 做成 property（而非类属性）才能跟随实例级的 DATA_DIR 覆盖。
    DATA_DIR: Path = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data"))
    FRONTEND_DIR: Path = BASE_DIR / "frontend"

    @property
    def RAW_DIR(self) -> Path:
        return self.DATA_DIR / "raw"

    @property
    def DB_PATH(self) -> Path:
        return self.DATA_DIR / "app.db"

    @property
    def is_default_data_dir(self) -> bool:
        """DATA_DIR 是否为默认值（多实例隔离的判断依据）。"""
        return self.DATA_DIR.resolve() == (BASE_DIR / "data").resolve()
    # 变更类接口（改配置/删文档/清记忆）的管理口令：公网部署必须设置（fail-closed）
    ADMIN_TOKEN: str | None = os.getenv("ADMIN_TOKEN")
    # 只读演示模式：公网隧道/录像时开启，上传/改配置/写记忆全部 403，问答与检索保持开放
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "0") == "1"

    @property
    def llm_ready(self) -> bool:
        """是否已填入可用的 key（含占位符检测）。"""
        key = self.API_KEY.strip()
        return bool(key) and not key.startswith("sk-在这里")

    def ensure_dirs(self) -> None:
        self.RAW_DIR.mkdir(parents=True, exist_ok=True)
        # MySQL + 非默认 DATA_DIR 的组合会串库：MySQL 的库名不随 DATA_DIR 变，
        # 两个实例共写同一张 rag_chunks 表、各自维护不同的向量文件 → 索引必然错乱
        #（实测：公告实例入库直接撞主实例主键）。sqlite 无此问题（DB_PATH 随 DATA_DIR）。
        if (self.DB_BACKEND or "sqlite").lower() == "mysql" and not self.is_default_data_dir:
            raise RuntimeError(
                f"MySQL 后端不支持多实例隔离：DATA_DIR={self.DATA_DIR} 但 MySQL 库仍是"
                f"「{self.MYSQL_DATABASE}」，两个实例会共写同一张表导致索引错乱。"
                "请改用 sqlite，或为第二实例单独建 MySQL 库并设 MYSQL_DATABASE。"
            )


settings = Settings()
settings.ensure_dirs()
