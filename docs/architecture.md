# 架构说明

> 本文讲**模块边界与关键设计决策**。运行方式见 [`../README.md`](../README.md)，
> Prompt 策略与踩坑记录见同目录的 [`prompt-strategy.md`](prompt-strategy.md) 与 [`pitfalls.md`](pitfalls.md)。

---

## 一、分层

```
前端（原生 HTML/CSS/JS，3 页）
   │  REST + SSE
   ▼
API 层  api/  ── chat · documents · config · stats · memories
   │         （只做参数校验与编排，不含业务规则）
   ▼
Agent 层  agent/  ── loop · prompts · gate · grounding · memory · rewrite · context
   │         （决定"问什么、取什么、怎么答"）
   ▼
能力层  rag/  ── loader · splitter · embedder · store · retriever · ingest
        tools/ ── registry · knowledge · finance · calc
   │
   ▼
存储层  db.py（会话/消息/记忆/配置） + rag/store.py（向量/切块元数据）
        ↕
外部    阿里云百炼（向量化 + 生成）· akshare（结构化财务数据）
```

## 二、模块职责边界（谁管什么，**不碰**什么）

| 模块 | 只负责 | 不碰 |
|---|---|---|
| `api/*` | HTTP 协议、参数校验、SSE 编码 | 业务规则、SQL |
| `agent/loop.py` | 一轮问答的编排（改写 → 预取 → 循环 → 校验） | 具体工具实现、SQL |
| `agent/gate.py` | "要不要取资料"的判断（纯函数，无副作用） | 取资料本身 |
| `agent/grounding.py` | 双通道取料与拼装 | 与模型交互 |
| `agent/memory.py` | 记忆策略（摘要阈值 / 提炼 / 注入） | 直接写 SQL（走 `db`） |
| `rag/*` | 文档 → 切块 → 向量 → 检索 | 会话与配置 |
| `tools/*` | 单个工具的实现与注册 | 编排 |
| `db.py` | 会话/消息/记忆/配置的持久化与统计 | **知识库统计**（属 `rag`，由 API 层去问） |

> 这条边界是有意为之：**"谁的数据谁统计"**。工作台里的文档/切块数由 `api/stats.py` 从 `rag.store` 取，
> 而 `db.py` 不 import 任何 rag 模块。

## 三、一次问答的数据流

```
1. 前端 POST /api/chat {message, conversation_id?}
2. api/chat.py
   ├─ 会话不存在 → 新建
   ├─ 先取历史（不含本轮）→ 避免本轮重复
   └─ 落库用户消息
3. agent/memory
   ├─ load_history()：返回「会话摘要 + 摘要之后的消息」
   └─ augment_system_prompt()：把「长期记忆」拼到系统提示词末尾
4. agent/loop.run()
   ├─ rewrite：多轮追问先改写成自包含问题（无历史则跳过）
   ├─ gate：判定是否需要资料（定性信号 / 数值信号 / 公司名 / 6 位代码）
   ├─ grounding：按需预取两条通道
   │    ├─ 通道 A：本地向量检索年报原文
   │    └─ 通道 B：解析股票代码 → 调 akshare 取结构化数据
   ├─ 循环（≤5 次模型调用）
   │    ├─ 模型决定是否再调工具（search_knowledge / get_stock_financials / calculate）
   │    ├─ 执行工具、回填结果（单条超长截断）
   │    └─ 无工具调用 → 本轮即最终回答
   └─ 校验
        ├─ 引用校验：回答里的「片段 N」必须在 citations 里
        └─ 接地校验：该取料却颗粒无收 → 替换为拒答
5. 落库回答 + 触发记忆摘要与提炼
```

**SSE 事件序列**：`meta` →（`rewrite`/`gate`/`citations`）→（`tool_call`/`tool_result`）→ `content` →（`warning`/`replace`）→ `end`

## 四、关键设计决策

### 4.1 双通道强制预取（核心）

**为什么**：把检索做成"模型自己调的工具"后，实测模型会跳过工具凭记忆作答（幻觉）。

**怎么做**：由**代码**判定并取料，按通道标注注入；模型只负责"基于资料作答 + 标注来源"。

**为什么分两条通道**：年报原文（可核对页码，但口径要自己辨）与结构化数据（字段期间明确）
的**可信度不同**，混在一起说会让读者分不清数字出处。

### 4.2 按页切分，不跨页合并

页码是引用锚点。跨页合并会让「这块属于哪一页」说不清。代价是短页会产生小块（实测最小 42 字），
换来引用 100% 可定位。

### 4.3 向量存 SQLite + numpy，不用向量数据库

约 2000 块规模下，暴力点积 < 5ms，与向量数据库没有实质差别；
换来省掉 `chromadb` 带来的数百 MB 依赖。

**不变式**：`row_id` = 向量数组下标。删除文档时压缩数组并重排 `row_id`，保证下标始终连续。
检索时先用 SQL 按条件筛出 `row_id`，再取数组对应行做点积。

### 4.4 云端 embedding + 本地检索

云端只做"把文本/查询变成向量"，**召回与比对全在本地**。
本机零算力依赖（不需 torch），检索是纯数学运算，本地毫秒级。

**一致性校验**：每个切块记 `embedding_model`；换模型等于换向量空间，新旧混用会**静默出错**，检索前要校验。

### 4.5 工具全部只读

越权不该靠"模型自觉"，而应靠**工具白名单** —— 系统里根本不存在写操作工具。

### 4.6 四层记忆

| 层 | 内容 | 存储 | 说明 |
|---|---|---|---|
| L1 | 单次请求内的消息与工具结果 | 进程内存 | 用完即弃 |
| L2 | 多轮原文 + **滚动摘要** | `conversations` / `messages` | 超阈值把最老一半压成摘要 |
| L3 | 年报切块与向量 | `vectors.npy` + `rag_chunks` | 重启不丢 |
| L4 | **关于用户**的偏好/常用公司/口径 | `memories` | 自动提炼、可见可删、注入 system prompt |

> 纪律：**L3 是语料，L4 是关于人**。把公司数据写进 L4 会在下次问别的公司时污染上下文。

## 五、约束与限制清单

| 项 | 值 | 在哪 |
|---|---|---|
| 单轮模型调用次数 | ≤ 5 | `.env` `MAX_MODEL_CALLS` |
| 单次回答输出上限 | 1500 token | `.env` `MAX_OUTPUT_TOKENS` |
| 单条工具结果上限 | 2400 字 | `.env` `TOOL_RESULT_MAX_CHARS` |
| 查询改写输出上限 | 120 token | `.env` `REWRITE_MAX_TOKENS` |
| 保留最近原文条数 | 0–30 | 配置页（后端钳制） |
| 触发压缩阈值 | 4–40，且必须 > 保留条数 | 配置页（后端钳制） |
| 支持格式 | 文字型 PDF（扫描件明确报错） | `rag/loader.py` |

## 六、可观测性

- `/health` —— 模型是否就绪、向量库后端、切块数
- `/api/stats` —— 会话/消息/token/工具调用/记忆/文档/切块 + 平均响应
- 每条回答的 `end` 事件带 `usage` / `context_tokens` / `prefetched` / `invalid_citations` / `ungrounded` / `truncated`
- 评测脚本 `scripts/eval.py` 会先**冻结配置快照**再跑，保证结果可比
