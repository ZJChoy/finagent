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
- [x] 20 题评测回归 → **待跑**（需要 API key 与网络，见下方"待办"）

