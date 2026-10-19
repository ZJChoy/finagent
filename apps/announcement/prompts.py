"""公告问答实例：人设与消息构造（应用层）。"""
from __future__ import annotations

SYSTEM_PROMPT = """\
你是上市公司公告分析助手。
你能做：基于系统提供的公告原文片段，回答关于公告内容的问题（决议、分红、回购、人事变动等），
并引用片段编号说明依据。
你不能做：预测股价或市值；回答与公告内容无关的问题；在片段中没有依据时编造细节。
硬规则：凡涉及公告具体内容，必须以检索到的片段为唯一依据；片段未覆盖的，如实说明。
输出要求：不超过三句话，先给结论，再给依据（引用片段编号）。
"""


def build_messages(query: str, history: list[dict] | None, system_prompt: str | None,
                   grounding: str | None = None) -> list[dict]:
    """框架钩子：组装 system + 历史 + （可选）预取接地块 + 用户问题。"""
    sys_prompt = system_prompt or SYSTEM_PROMPT
    parts = [{"role": "system", "content": sys_prompt}]
    parts += (history or [])
    if grounding:
        parts.append({"role": "system", "content": f"以下是本轮预取的公告原文片段，回答必须以它们为依据：\n{grounding}"})
    parts.append({"role": "user", "content": query})
    return parts
