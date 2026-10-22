# FinAgent 改造记录

> 目标：定位 A（作品集 Demo）→ 良好线 + 1 项 stretch。
> 原则：先修 bug 再补测试再谈指标；每个 P0 修复先写失败测试再修；每个里程碑结束跑一次 20 题评测对比。
> 本文档持续记录：改了什么、为什么、验收结果、留下的决策。

## 基线快照（2026-10-22，改造前）

- 全量 review 发现 6 个明确 bug（详见对话记录），关键项：
  1. `calculate` 负数丢符号（`-0.5 → "0.5"`）
  2. 索引变体（llmctx）与写入路径不一致：上传后检索 `IndexError`，删除后静默错配
  3. 工具失败返回 `ok=True`，接地门控被架空（calculate 失败也算"接地"）
  4. 实体一致性校验对映射表之外的公司失效；代码正则中文相邻识别不到
  5. `DATA_DIR` 不隔离 `RAW_DIR` 与 MySQL 元数据，第二实例与主实例串数据
  6. `DELETE /api/conversations/{cid}` 无鉴权
- 生产索引完好性核对：`rag_chunks` 2104 行、`max(row_id)=2103`、4 份文档 699+646+378+381=2104，与向量文件 `(2104, 1024)` 一致。
- 评测基线：自动初判 20/20（eval/results-自研版-基线.md）。
- 测试现状：0 个 pytest 测试（仅 scripts/test_parse_guard.py 手工脚本）。

## 里程碑计划

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 止血 | 测试骨架+CI；修负数符号 / ok 契约 / 实体守卫 / 删除鉴权 | 进行中 |
| M1 一致性与复现 | 索引清单与失效、多实例隔离、锁依赖、Makefile、启动自检 | 未开始 |
| M2 最小安全 | 配置页口令、DEMO_MODE、限流、请求 ID 日志 | 未开始 |
| M3 质量阈值 | 相关性阈值（stretch）、混合检索离线评估（可弃）、评测扩展 | 未开始 |
| M4 证据收口 | README 重写、results.md 台账、仓库卫生、演示视频、简历条目 | 未开始 |

---

## 变更日志

### M0.1 测试骨架 + CI ✅（commit 9da9120 + 99539b5）

- `tests/unit/` 七个测试文件、67 个用例：calc 格式化契约、工具 ok 契约、
  实体一致性校验、配置钳制、splitter 长度上界、runtime 接地门控（假 LLM）、
  API 鉴权契约（TestClient）、参数解析护栏 pytest 化。
- `tests/conftest.py`：环境隔离（临时 DATA_DIR + sqlite + 固定 ADMIN_TOKEN），
  测试永不触碰真实数据与 MySQL。
- `.github/workflows/ci.yml`：ruff + pytest，Python 3.11/3.12 矩阵。
- ruff 全量清理 79 处（36 自动修复）；pyproject 补回 `finagent.core.retriever`
  子包（原配置 `pip install .` 会缺包）。

**一个设计决策（记录）**：测试最初的 13 个失败正是 review 发现的缺陷清单，
采用 TDD——先让测试钉住错误行为，修复后再全绿。此后任何回归都会直接红。

### M0.2 calculate 负数丢符号 ✅（commit 5d684c6）

- 根因：`(-1, 0)` 区间的 `whole` 是 `"-0"`，`int("-0") == 0` 抹掉符号。
- 修法：先取绝对值格式化再补符号；极小值（round 6 位后归零、repr 出
  科学计数法）定点展开保留量级。
- 契约测试：`-0.5 → "-0.5"`、`-0.00005 → "-0.00005"`、`1/3 → "0.333333"`、
  大数千分位不变（172,054,171,890.91）。

### M0.3 工具 ok 契约 + 接地门控收紧 ✅（commit 5d684c6）

- `registry.call` 缺 `ok` 字段时默认 **False**（原默认 True——失败被替工具
  声明成功）。calc/finance 失败分支显式 `ok: False`；knowledge/公告检索的
  空结果显式 `ok: True`（空结果是合法回答）。
- **接地证据收紧**（runtime）：只有「证据类工具（名字含 search/query/get/
  fetch/retrieve）成功调用**且带回 citations**」才算接地证据。
  - calculate 成败都不算证据（算术不是资料）；
  - "检索了但一无所获"不算证据（这正是拒答门存在的意义）。
  - trace 现在记录每条工具调用的 citations 数量。
- **验证**：假 LLM 测试复现原缺陷（calculate 失败 → grounded=True → 幻觉
  放行），修后同场景返回拒答文案。

### M0.4 实体一致性校验补洞 ✅（commit 5d684c6）

- 原实现：检索命中代码若不在 COMPANY_CODE_MAP 内，`canon` 为空直接放行
  → 问"格力"命中伊利年报会捞出 000858（原事故的表外版本）。
- 修法：命中代码不在映射表内一律判"未收录"；映射表内公司仍校验
  问句提及。
- 顺带修 `_CODE_IN_QUERY` 的 `\b`：Unicode 语义下中文与数字相邻不构成
  词边界（"查一下600519的营收"解析不到），改环视 `(?<!\d)...(?!\d)`。
- 行为变化（有意）：表外公司即使问句点名也拒答（未收录语义），
  与"本系统仅收录茅台、美的"的既有话术一致。

### M0.5 删除会话鉴权 ✅（commit ac37c8e）

- `DELETE /api/conversations/{cid}` 加 `Depends(require_admin)`，与其它
  变更类接口对齐；`test_api_security.py` 钉住全部变更类接口的
  403/放行契约。
- 注意（遗留）：前端删除会话按钮现在会 403——M2.1 配置页口令方案
  会一并处理对话页的删除体验。

### M0 验收

- [x] pytest 67/67 绿，1.2s，离线
- [x] ruff 0 error
- [ ] 20 题评测回归 → 推迟到 M1 完成后与 M3 前基线一起跑（避免重复消耗 API 额度）

---

## M1 一致性与复现 —— ✅ 全部完成

### M1.1 索引变体失效机制 ✅（commit 934889a）

**根因链**（review 实测复现）：读索引走变体（`RETRIEVAL_INDEX=llmctx`），写索引
只写 base → 上传后变体行数落后，`search` 越界（被 preflight 吞成"静默失效"）；
删除后 row_id 重排、变体没重排 → 向量与文本**静默错配**（正常返回错内容）；
reset 重建后行数可能恰好相同，行数校验挡不住内容错位。

**修法**（失效回退而非自动重建——重建要逐块调 LLM，成本高、失败面大）：
- 每个变体文件配 `.fp` 指纹侧车：`行数:row_id,列表`。写入路径
  （add/delete/reset）后指纹自然过期；
- `active_vector_path()` 校验「变体存在 + 行数一致 + 指纹一致」，不满足回退
  base——质量可以退化，错位绝不允许；
- `index_health()` 挂进 `/health`：`meta_rows / base_rows / consistent /
  active_variant / variant_rows / stale_variants`；
- `scripts/index_doctor.py`：体检 + `--stamp` 给存量同批变体补登记指纹；
- `scripts/contextual_index.py` 两个 build 入口写完即登记指纹。

**关键决策**：指纹用 `行数:row_id 列表` 而不是纯行数——reset 后重建时行数
可能恰好相同（行数校验测试确实漏过了这种错位），row_id 列表能区分
"同一批切块"与"不同切块"。

**存量迁移**：本机 llmctx（2104 行，与元数据一致）已 `--stamp` 登记并恢复生效。

### M1.2 多实例隔离 ✅（commit a00c1a9）

- `RAW_DIR` 从硬编码 `data/raw` 改为 property 跟随 `DATA_DIR`（原实现第二实例
  上传的 PDF 会落进主实例语料目录）；`DB_PATH` 同理。
- **MySQL + 非默认 DATA_DIR 组合在启动时直接拒绝**（`ensure_dirs` raise）：
  MySQL 库名不随 DATA_DIR 变，两实例共写一张 `rag_chunks` 表、各自维护不同
  `.npy` → 索引必然错乱。review 时实测公告实例入库撞主实例主键才暴露。
- sqlite 不受影响（DB_PATH 随 DATA_DIR，天然隔离）。
- 测试：`test_config_paths.py` 5 用例。

### M1.3 依赖锁定 + Makefile ✅（commit 0ff95f2）

- `requirements.lock`：本机实测组合（fastapi 0.150.0 / uvicorn 0.54.0 /
  openai 3.35.1 / numpy 2.5.4 / akshare 1.19.1 等），逐包核对过安装版本。
- `make bootstrap / serve / test / lint / eval / doctor`。
- 复现路径：clone → 配 `.env` → `make bootstrap` → `make serve`。

### M1.4 启动自检 ✅（commit 2f8e1b7）

- base 与元数据行数不一致 → **FATAL 拒绝启动**（exit 2）：带着错位索引服务
  必然答错，比宕机更糟的是静默答错。实测坏索引启动被拦截。
- stale 变体 / 未配置 API key / 公网绑定无 ADMIN_TOKEN → WARN（stderr，JSON）。
- 全部结构化 JSON 输出，公网部署可 grep。

### M1 验收

- [x] pytest 79/79 绿；ruff 0 error
- [x] 上传/删除文档后检索永不越界、永不静默错配（7 个 store 用例覆盖）
- [x] `/health` 报索引一致性
- [x] 第二实例 sqlite 隔离成立；MySQL 组合拒绝启动
- [x] 依赖锁定 + Makefile
- [ ] 20 题评测回归（与 M3 一起跑）

---

## M2 最小安全与演示形态 —— ✅ 全部完成

### M2.1 前端管理口令流 ✅（commit fa337a2）

修复 review 中确认的"设置 ADMIN_TOKEN 后配置页 100% 不可用"自相矛盾：
- `app.js` 的 `api()` 对 POST/PUT/DELETE 自动带 `X-Admin-Token`
  （sessionStorage，关浏览器即失效）；403 抛 `AdminRequiredError`，
  `ensureAdminToken()` 就地 prompt 输入并重试一次；
- config.html（保存/加删清记忆）、knowledge.html（上传/删文档）、
  index.html（删会话，配合 M0.5）全页面接入。
- 决策：sessionStorage 只防误触不防攻击——与 README 声明的 threat model 一致。

### M2.2 DEMO_MODE 只读演示 ✅（commit 9cefb4e）

`require_admin` 首先检查 `DEMO_MODE=1`：变更类接口一律 403（口令也不放行），
问答与检索开放。公网隧道/录像专用。3 个新测试钉住契约。

### M2.3 限流 + search 边界 ✅（commit 2da12b8）

- `backend/api/ratelimit.py`：令牌桶按 `scope:IP`（X-Forwarded-For 优先），
  `/api/chat` 10 桶/0.2 每秒回填、`/api/documents/search` 20 桶/0.5 回填，
  超限 429 + Retry-After。
- **实现期修了一个 off-by-one**：首请求创建满桶导致实际突发上限
  capacity+1——改为首请求视为已消耗一个令牌（测试暴露后修复）。
- `SearchIn` 收紧：`top_k ∈ [1,20]`（原 `top_k=-1` 会让切片返回近全库）、
  `query ≤ 2000` 字。原实现完全无校验。

### M2.4 请求 ID + 阶段日志 ✅（commit 1dc9f48）

- `requestlog.py`：ContextVar 请求 ID + JSON 行 stdout；
- chat 四类事件（start/tool_call/done/error），done 带延迟/token/引用数/拒答标记；
- **隐私边界**：只落标量（长度/计数/耗时/错误类型），不落用户问题与回答原文。

### M2 验收（含真实起服务冒烟）

- [x] pytest 89/89 绿；ruff 0 error
- [x] 冒烟（sqlite + 临时 DATA_DIR 起 uvicorn 8765）：`/health` 报索引一致性，
  无 token 删除会话 403，`top_k=0` 422，正常检索 200
- [x] 配置页/知识库页/对话页 403 → 口令输入闭环（静态语法检查通过）



