/* 共享工具：Markdown 渲染、请求封装、导航与健康状态 */

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function inlineFmt(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
}

/* 极简 Markdown：代码块 / 标题 / 列表 / 行内代码 / 加粗。先转义，防 XSS。 */
function md(src) {
  src = src || "";
  const blocks = [];
  src = src.replace(/```[^\n]*\n?([\s\S]*?)```/g, (m, code) => {
    blocks.push("<pre><code>" + escapeHtml(code.replace(/\n$/, "")) + "</code></pre>");
    return "\u0000" + (blocks.length - 1) + "\u0000";
  });
  const out = [];
  let listOpen = false;
  const closeList = () => { if (listOpen) { out.push("</ul>"); listOpen = false; } };
  for (const line of src.split("\n")) {
    if (/^\u0000\d+\u0000$/.test(line.trim())) { closeList(); out.push(line.trim()); continue; }
    if (!line.trim()) { closeList(); continue; }
    if (/^###\s+/.test(line)) { closeList(); out.push("<h3>" + inlineFmt(line.replace(/^###\s+/, "")) + "</h3>"); continue; }
    if (/^##\s+/.test(line)) { closeList(); out.push("<h2>" + inlineFmt(line.replace(/^##\s+/, "")) + "</h2>"); continue; }
    if (/^\s*[-*]\s+/.test(line)) {
      if (!listOpen) { out.push("<ul>"); listOpen = true; }
      out.push("<li>" + inlineFmt(line.replace(/^\s*[-*]\s+/, "")) + "</li>");
      continue;
    }
    closeList();
    out.push("<p>" + inlineFmt(line) + "</p>");
  }
  closeList();
  return out.join("\n").replace(/\u0000(\d+)\u0000/g, (m, i) => blocks[i]);
}

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

function topbar(active) {
  const links = [
    ["/", "chat", "对话"],
    ["/knowledge", "knowledge", "知识库"],
    ["/config", "config", "配置"],
  ];
  return (
    '<div class="topbar">' +
    '<div class="brand">Fin<span>Agent</span></div>' +
    '<nav class="nav">' +
    links.map(([href, page, label]) =>
      `<a href="${href}" data-page="${page}" class="${page === active ? "active" : ""}">${label}</a>`
    ).join("") +
    '</nav><div class="spacer"></div>' +
    '<span id="llm-badge" class="badge">检测中…</span>' +
    "</div>"
  );
}

async function loadHealth() {
  const el = document.getElementById("llm-badge");
  if (!el) return;
  try {
    const h = await api("GET", "/health");
    if (h.llm_ready) {
      el.className = "badge ok";
      el.textContent = "模型已就绪 · " + h.chat_model;
    } else {
      el.className = "badge warn";
      el.textContent = "未配置 API key";
    }
  } catch (e) {
    el.className = "badge warn";
    el.textContent = "服务异常";
  }
}
