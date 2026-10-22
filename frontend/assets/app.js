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

/* 变更类接口的管理口令：页面输入一次，存 sessionStorage（关浏览器即失效）。
   sessionStorage 只防误触不防攻击——与本项目 threat model 一致（见 README 安全节）。 */
function adminToken() {
  return sessionStorage.getItem("finagent_admin") || "";
}

function setAdminToken(t) {
  sessionStorage.setItem("finagent_admin", t || "");
}

/* 统一请求封装：变更类方法自动带 X-Admin-Token；403 时提示输入口令。 */
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (["POST", "PUT", "DELETE"].includes(method) && adminToken()) {
    opt.headers["X-Admin-Token"] = adminToken();
  }
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch (_) { /* 非 JSON 响应 */ }
    if (r.status === 403) {
      throw new AdminRequiredError(detail || "需要管理口令");
    }
    throw new Error(detail || (await r.text()) || r.statusText);
  }
  return r.json();
}

class AdminRequiredError extends Error {
  constructor(msg) { super(msg); this.name = "AdminRequiredError"; }
}

/* 403 时弹出就地口令输入（各页面共用）。确认后重试 once。 */
async function ensureAdminToken(retryFn) {
  const t = prompt("该操作需要管理口令（.env 里的 ADMIN_TOKEN）：");
  if (t === null) return false;   // 用户取消
  setAdminToken(t.trim());
  if (retryFn) { try { await retryFn(); return true; } catch (e) { alert("失败：" + e.message); } }
  return true;
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
