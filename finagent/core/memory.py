"""会话记忆（框架层）：L2 会话摘要 + L4 长期记忆。

- L2 摘要：会话消息超过阈值时，把最老的一半压成一段摘要存库，只留最近几轮原文。
- L4 长期记忆：每积累若干轮，从对话里提炼「偏好 / 常用公司 / 显示口径」，注入 system prompt（限 300 token）。
- 纪律：记忆必须**可见可删**（应用层负责暴露管理接口）；领域语料属于 L3，不是 L4，两者别混谈。

框架层不感知存储与配置：db 适配器（需实现 get_config / get_summary / list_messages /
set_summary / count_user_messages / add_memory / list_memories 七个函数）、llm 模块
（需实现 complete）与参数在构造时注入。
"""
from __future__ import annotations

import json

KEEP_RECENT = 6        # 保留最近 3 轮原文（默认值）
SUMMARIZE_AT = 12      # 超过这么多条就压缩（默认值）
EXTRACT_EVERY = 3      # 每积累 3 条用户消息提炼一次（默认值）
MEMORY_MAX_CHARS = 450  # ≈300 token

_SUMMARY_SYS = "你把财报分析对话压缩成一段简明摘要，保留：讨论的公司、报告期、关键结论与数字、用户关注点。不要评论。"
_EXTRACT_SYS = (
    "从对话中提炼关于**用户本人**的长期信息（不是公司信息），例如常用公司、回答偏好、习惯口径。"
    '只输出 JSON 数组，每项形如 {"kind":"常用公司","content":"贵州茅台（600519）"}，'
    "kind 只能是：偏好 / 常用公司 / 显示口径。最多 3 项，没有就输出 []。不要输出多余文字。"
)


class SessionMemory:
    def __init__(self, *, db, llm_mod, rewrite_model: str, default_system_prompt: str | None = None):
        self._db = db
        self._llm = llm_mod
        self._rewrite_model = rewrite_model
        self._default_system_prompt = default_system_prompt
        self._extracted: set[tuple[int, int]] = set()  # (conv_id, user_msg_count) 已提炼过

    def _params(self) -> tuple[bool, int, int, int]:
        """从配置存储读记忆策略参数：返回 (是否启用, 保留条数, 压缩阈值, 提炼间隔)。

        ⚠️ 用 is None 判断而非 `or`：keep_recent=0（全部压缩）是合法配置，
        `0 or KEEP_RECENT` 会把用户设置吞掉换成默认值。
        """
        try:
            cfg = self._db.get_config()
        except Exception:  # noqa: BLE001
            return True, KEEP_RECENT, SUMMARIZE_AT, EXTRACT_EVERY

        # keep_recent 允许 0（全部压缩）；summarize_at / extract_every 的 0 无意义
        keep = cfg.get("memory_keep_recent")
        keep = KEEP_RECENT if keep is None else int(keep)
        return (
            bool(cfg.get("memory_enabled", True)),
            keep,
            int(cfg.get("memory_summarize_at") or SUMMARIZE_AT),
            int(cfg.get("memory_extract_every") or EXTRACT_EVERY),
        )

    def load_history(self, conv_id: int) -> tuple[str, list[dict]]:
        """返回 (摘要, 摘要之后的历史消息)，供 Agent 作为上下文。"""
        summary, upto = self._db.get_summary(conv_id)
        msgs = self._db.list_messages(conv_id, after_id=upto)
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in msgs
            if m["role"] in ("user", "assistant") and m["content"]
        ]
        return summary, history

    def maybe_summarize(self, conv_id: int) -> None:
        """最老一半压成摘要（保留最近 keep_recent 条原文）。失败不影响对话。"""
        try:
            enabled, keep_recent, summarize_at, _ = self._params()
            if not enabled:
                return
            summary, upto = self._db.get_summary(conv_id)
            msgs = self._db.list_messages(conv_id, after_id=upto)
            if len(msgs) <= summarize_at:
                return
            to_compress = msgs[:-keep_recent] if keep_recent > 0 else msgs
            if not to_compress:
                return
            body = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in to_compress)
            new_part = self._llm.complete(
                [
                    {"role": "system", "content": _SUMMARY_SYS},
                    {"role": "user", "content": f"已有摘要：\n{summary or '（无）'}\n\n新对话：\n{body}"},
                ],
                model=self._rewrite_model,
                max_tokens=400,
            )
            if new_part:
                self._db.set_summary(conv_id, new_part.strip(), to_compress[-1]["id"])
        except Exception:  # noqa: BLE001
            pass

    def maybe_extract(self, conv_id: int) -> None:
        """每积累 extract_every 条用户消息提炼一次长期记忆。"""
        try:
            enabled, _, _, extract_every = self._params()
            if not enabled or extract_every <= 0:
                return
            n = self._db.count_user_messages(conv_id)
            if n == 0 or n % extract_every != 0:
                return
            key = (conv_id, n)
            if key in self._extracted:
                return
            self._extracted.add(key)

            msgs = self._db.list_messages(conv_id)
            body = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in msgs[-12:])
            raw = self._llm.complete(
                [{"role": "system", "content": _EXTRACT_SYS},
                 {"role": "user", "content": body}],
                model=self._rewrite_model,
                max_tokens=200,
            )
            items = parse_items(raw)
            for it in items[:3]:
                self._db.add_memory(it.get("kind", "偏好"), it.get("content", ""), conv_id)
        except Exception:  # noqa: BLE001
            pass

    def memory_block(self) -> str:
        """把长期记忆拼成一段可注入 system prompt 的文字（限 300 token 左右）。"""
        items = self._db.list_memories()
        if not items:
            return ""
        lines, size = [], 0
        for m in items:
            line = f"- （{m['kind']}）{m['content']}"
            if size + len(line) > MEMORY_MAX_CHARS:
                break
            lines.append(line)
            size += len(line)
        return "\n【关于用户的长期记忆】\n" + "\n".join(lines) if lines else ""

    def augment_system_prompt(self, base: str | None) -> str | None:
        block = self.memory_block()
        if not block:
            return base
        return (base or self._default_system_prompt) + block


def parse_items(raw: str) -> list[dict]:
    """解析提炼结果里的 JSON 数组（容忍模型输出多余文字）。"""
    raw = (raw or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("content")]
