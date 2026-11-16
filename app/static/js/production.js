(() => {
  "use strict";

  const app = document.getElementById("prod-app");
  if (!app) return;

  const BASE = app.dataset.base || "";
  const NFC = "http://127.0.0.1:8765";
  const state = {
    tasks: [],
    summary: null,
    filter: "all",
    query: "",
    selectedId: null,
    busy: false,
  };

  const listEl = document.getElementById("prod-list");
  const countEl = document.getElementById("prod-count");
  const detailEl = document.getElementById("prod-detail-body");
  const emptyEl = document.getElementById("prod-detail-empty");
  const statsEl = document.getElementById("prod-stats");
  const nfcEl = document.getElementById("nfc-status");
  const nfcText = document.getElementById("nfc-text");
  const searchEl = document.getElementById("prod-search");
  const percentEl = document.getElementById("prod-percent");
  const barEl = document.getElementById("prod-progress-bar");
  const updatedEl = document.getElementById("prod-updated");
  const filterEl = document.getElementById("prod-filter");

  const STAGE_LABEL = {
    pending: "待制作",
    written: "已写卡",
    printed: "已打印",
    done: "已完成",
  };
  const STAGE_ORDER = { pending: 0, written: 1, printed: 2, done: 3 };

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message =
        payload.error || payload.detail || `请求失败（HTTP ${response.status}）`;
      throw new Error(typeof message === "string" ? message : "请求失败");
    }
    return payload;
  }

  function selectedTask() {
    return state.tasks.find((task) => task.id === state.selectedId) || null;
  }

  function filteredTasks() {
    const query = state.query.trim().toLowerCase();
    return state.tasks.filter((task) => {
      if (state.filter !== "all") {
        if (state.filter === "in_progress") {
          if (!["written", "printed"].includes(task.stage)) return false;
        } else if (task.stage !== state.filter) {
          return false;
        }
      }
      if (!query) return true;
      return (
        String(task.id).includes(query) ||
        task.qr_code.toLowerCase().includes(query) ||
        task.group_no.toLowerCase().includes(query)
      );
    });
  }

  function renderStats() {
    const summary = state.summary || {};
    statsEl.querySelectorAll("[data-stat]").forEach((node) => {
      const key = node.dataset.stat;
      node.textContent = summary[key] ?? "—";
    });
    const total = Number(summary.total || 0);
    const percent = total ? Math.round((Number(summary.done || 0) / total) * 100) : 0;
    percentEl.textContent = `${percent}%`;
    barEl.style.width = `${percent}%`;
    if (updatedEl) {
      updatedEl.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", {
        hour12: false,
      })}`;
    }
  }

  function stageBadge(task) {
    return `<span class="prod-stage prod-stage-${esc(task.stage)}">${esc(
      task.stage_label || STAGE_LABEL[task.stage] || task.stage
    )}</span>`;
  }

  function renderList() {
    const tasks = filteredTasks();
    countEl.textContent = `${tasks.length} / ${state.tasks.length}`;
    if (!tasks.length) {
      listEl.innerHTML = `<div class="prod-empty"><span class="prod-empty-mark">∅</span><strong>没有符合条件的任务</strong><p>换一个筛选条件或清空搜索。</p></div>`;
      return;
    }
    listEl.innerHTML = tasks
      .map(
        (task) => `
        <button class="prod-item ${task.id === state.selectedId ? "is-selected" : ""} ${
          task.stage === "done" ? "is-done" : ""
        }" data-id="${task.id}" type="button" style="--group-color:${esc(task.group_color)}">
          <span class="prod-item-color"></span>
          <span class="prod-item-main">
            <span class="prod-item-top">
              <strong>${esc(task.group_no)}</strong>
              <span class="prod-item-id">#${String(task.id).padStart(2, "0")}</span>
            </span>
            <span class="prod-item-code">${esc(task.qr_code)}</span>
            <span class="prod-item-meta">
              ${stageBadge(task)}
              ${task.last_error ? '<span class="prod-error-dot">异常</span>' : ""}
            </span>
          </span>
          <svg class="prod-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>
        </button>`
      )
      .join("");
  }

  function timeline(task) {
    const current = STAGE_ORDER[task.stage] ?? 0;
    const steps = [
      ["written", "写卡"],
      ["printed", "打印"],
      ["done", "贴牌"],
    ];
    return `<div class="prod-timeline">${steps
      .map(([key, label], index) => {
        const reached = current >= index + 1;
        return `<span class="${reached ? "is-reached" : ""}"><i>${reached ? "✓" : index + 1}</i>${label}</span>`;
      })
      .join("")}</div>`;
  }

  function renderDetail() {
    const task = selectedTask();
    if (!task) {
      detailEl.hidden = true;
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    detailEl.hidden = false;
    const qrUrl = `${BASE}/api/tasks/${task.id}/qr.svg`;
    const canPrint = ["written", "printed"].includes(task.stage);
    const canDone = task.stage === "printed" || task.stage === "done";
    detailEl.innerHTML = `
      <div class="prod-detail-head" style="--group-color:${esc(task.group_color)}">
        <div>
          <span class="eyebrow">TASK #${String(task.id).padStart(2, "0")}</span>
          <h2>${esc(task.group_no)} · ${esc(task.qr_code)}</h2>
          <div class="prod-detail-badges">${stageBadge(task)} ${
            task.last_error ? '<span class="prod-stage prod-stage-error">有异常</span>' : ""
          }</div>
        </div>
        <span class="prod-detail-color"></span>
      </div>

      ${task.last_error ? `<div class="prod-alert"><strong>上次异常</strong><p>${esc(task.last_error)}</p></div>` : ""}

      <div class="prod-detail-grid">
        <figure class="prod-qr">
          <img src="${qrUrl}" alt="击杀码 ${esc(task.qr_code)}" width="220" height="220">
          <figcaption>击杀码 <strong>${esc(task.qr_code)}</strong></figcaption>
        </figure>
        <div class="prod-detail-info">
          <div class="prod-field"><span>组别</span><strong>${esc(task.group_no)}</strong></div>
          <div class="prod-field"><span>NTAG token</span><code title="${esc(task.ntag_token)}">${esc(task.ntag_token)}</code></div>
          ${timeline(task)}
        </div>
      </div>

      <div class="prod-actions">
        <button class="btn btn-primary" data-action="write" type="button" ${state.busy ? "disabled" : ""}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h10M4 17h7"/></svg>
          ${task.stage === "pending" ? "写入 NTAG" : "重写 NTAG"}
        </button>
        <button class="btn btn-secondary" data-action="print" type="button" ${canPrint ? "" : "disabled"}>
          打印标签
        </button>
        <button class="btn btn-secondary" data-action="printed" type="button" ${canPrint ? "" : "disabled"}>
          已打印
        </button>
        <button class="btn btn-secondary" data-action="done" type="button" ${canDone ? "" : "disabled"}>
          完成贴牌
        </button>
      </div>
      <div class="prod-actions prod-actions-sub">
        <button class="btn btn-ghost btn-sm" data-action="error" type="button">记录异常</button>
        <button class="btn btn-ghost btn-sm" data-action="manual-written" type="button">无读卡器 · 手动标记已写卡</button>
        <button class="btn btn-danger btn-sm" data-action="reset" type="button">重置任务</button>
      </div>
      <p class="prod-hint">先写卡并回读成功，再打印；打印后确认贴牌。任何步骤失败都可记录异常并重置重做。</p>
    `;
  }

  function render() {
    renderStats();
    renderList();
    renderDetail();
  }

  async function load() {
    try {
      const [tasks, summary] = await Promise.all([
        api("/api/production/tasks?status=all"),
        api("/api/production/status"),
      ]);
      state.tasks = Array.isArray(tasks) ? tasks : [];
      state.summary = summary;
      if (!state.selectedId && state.tasks.length) {
        state.selectedId = state.tasks[0].id;
      }
      if (state.selectedId && !selectedTask()) {
        state.selectedId = state.tasks.length ? state.tasks[0].id : null;
      }
      render();
    } catch (error) {
      listEl.innerHTML = `<div class="prod-empty"><span class="prod-empty-mark">!</span><strong>无法读取制作任务</strong><p>${esc(
        error.message
      )}</p></div>`;
    }
  }

  async function mark(taskId, stage, error) {
    state.busy = true;
    renderDetail();
    try {
      await api(`/api/production/${taskId}/mark`, {
        method: "POST",
        body: JSON.stringify({ stage, error: error || null }),
      });
      await load();
    } catch (err) {
      window.alert(err.message);
    } finally {
      state.busy = false;
      renderDetail();
    }
  }

  async function checkNfc() {
    nfcEl.dataset.state = "checking";
    nfcText.textContent = "检测中…";
    try {
      const response = await fetch(`${NFC}/health`, { cache: "no-store" });
      const data = await response.json();
      if (data.ok && data.reader_connected) {
        nfcEl.dataset.state = "ready";
        nfcText.textContent = data.reader || "读卡器已连接";
      } else {
        nfcEl.dataset.state = "offline";
        nfcText.textContent = data.error || "未检测到读卡器";
      }
    } catch (error) {
      nfcEl.dataset.state = "offline";
      nfcText.textContent = "本机 NFC 服务未启动（127.0.0.1:8765）";
    }
  }

  async function writeNfc(task) {
    if (state.busy) return;
    state.busy = true;
    renderDetail();
    try {
      const health = await fetch(`${NFC}/health`, { cache: "no-store" }).then((r) =>
        r.json()
      );
      if (!health.ok || !health.reader_connected) {
        throw new Error(health.error || "读卡器未连接");
      }
      const response = await fetch(`${NFC}/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ntag_token: task.ntag_token }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "写卡失败");
      }
      await api(`/api/production/${task.id}/mark`, {
        method: "POST",
        body: JSON.stringify({ stage: "written" }),
      });
      await load();
    } catch (error) {
      window.alert(`写卡失败：${error.message}\n\n可检查读卡器后重试，或使用“手动标记已写卡”。`);
    } finally {
      state.busy = false;
      renderDetail();
    }
  }

  async function printTask(task) {
    const win = window.open("", "_blank", "width=760,height=900");
    if (!win) {
      window.alert("浏览器阻止了打印窗口，请允许弹窗后重试。");
      return;
    }
    win.document.write("<p style='font-family:sans-serif;padding:24px'>正在准备标签…</p>");
    const qrUrl = new URL(`${BASE}/api/tasks/${task.id}/qr.svg`, window.location.origin).href;
    let qrDataUrl = "";
    try {
      const qrResponse = await fetch(qrUrl, { credentials: "same-origin" });
      if (!qrResponse.ok) throw new Error(`二维码加载失败（HTTP ${qrResponse.status}）`);
      const svg = await qrResponse.text();
      qrDataUrl = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`;
    } catch (error) {
      win.close();
      window.alert(error.message);
      return;
    }
    win.document.open();
    win.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
      <title>${esc(task.group_no)} ${esc(task.qr_code)}</title>
      <style>
        @page { size: A6; margin: 10mm; }
        * { box-sizing: border-box; }
        body { margin: 0; font-family: "PingFang SC","Microsoft YaHei",sans-serif; color: #111; }
        .card { width: 100%; border: 3px solid #111; border-radius: 8mm; padding: 7mm; text-align: center; }
        .brand { font-size: 10mm; font-weight: 900; letter-spacing: -0.5mm; }
        .group { display: inline-block; margin: 3mm 0 4mm; padding: 1.5mm 5mm; border-radius: 999px; color: #fff; background: ${esc(task.group_color)}; font-size: 7mm; font-weight: 800; }
        img { width: 62mm; height: 62mm; display: block; margin: 0 auto; }
        .code { margin-top: 3mm; font-family: monospace; font-size: 13mm; font-weight: 900; letter-spacing: 2mm; }
        .hint { margin-top: 2mm; font-size: 3.4mm; color: #555; }
        .id { margin-top: 4mm; font-family: monospace; font-size: 3.2mm; color: #777; }
      </style></head><body>
      <div class="card">
        <div class="brand">RIP TAG</div>
        <div class="group">${esc(task.group_no)}</div>
        <img src="${qrDataUrl}" alt="${esc(task.qr_code)}">
        <div class="code">${esc(task.qr_code)}</div>
        <div class="hint">扫码或手输击杀码</div>
        <div class="id">TASK-${String(task.id).padStart(3, "0")}</div>
      </div>
      <script>window.onload=function(){setTimeout(function(){window.print();},350);};</script>
      </body></html>`);
    win.document.close();
  }

  filterEl.addEventListener("click", (event) => {
    const button = event.target.closest("[data-filter]");
    if (!button) return;
    state.filter = button.dataset.filter;
    filterEl.querySelectorAll("[data-filter]").forEach((node) => {
      node.classList.toggle("is-active", node === button);
    });
    renderList();
  });

  searchEl.addEventListener("input", () => {
    state.query = searchEl.value;
    renderList();
  });

  listEl.addEventListener("click", (event) => {
    const button = event.target.closest("[data-id]");
    if (!button) return;
    state.selectedId = Number(button.dataset.id);
    render();
  });

  detailEl.addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    const task = selectedTask();
    if (!action || !task) return;
    if (action === "write") return writeNfc(task);
    if (action === "print") return printTask(task);
    if (action === "printed") return mark(task.id, "printed");
    if (action === "done") return mark(task.id, "done");
    if (action === "manual-written") return mark(task.id, "written");
    if (action === "error") {
      const message = window.prompt("记录异常（不会推进制作阶段）：", task.last_error || "");
      if (message === null || !message.trim()) return;
      const stage = task.stage === "pending" ? "written" : task.stage;
      return mark(task.id, stage, message.trim());
    }
    if (action === "reset") {
      if (!window.confirm(`确定重置 #${task.id}（${task.group_no} / ${task.qr_code}）？`)) return;
      state.busy = true;
      renderDetail();
      api(`/api/production/${task.id}/reset`, { method: "POST" })
        .then(load)
        .catch((error) => window.alert(error.message))
        .finally(() => {
          state.busy = false;
          renderDetail();
        });
    }
  });

  const npcModal = document.getElementById("npc-modal");
  const npcList = document.getElementById("npc-list");
  const npcOpen = document.getElementById("npc-open");

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      window.alert("已复制");
    } catch {
      window.prompt("复制 NPC URL：", text);
    }
  }

  async function writeNpcTag(npc, button) {
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "写卡中…";
    try {
      const health = await fetch(`${NFC}/health`, { cache: "no-store" }).then((r) =>
        r.json()
      );
      if (!health.ok || !health.reader_connected) {
        throw new Error(health.error || "读卡器未连接");
      }
      const response = await fetch(`${NFC}/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ npc_token: npc.token }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "写卡失败");
      }
      window.alert(
        `NPC「${npc.name}」写卡成功\n\n${payload.readback_url}\n\n请用手机贴卡确认能打开 NPC 页面。`
      );
    } catch (error) {
      window.alert(`NPC 写卡失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = oldText;
    }
  }

  function renderNpcs(npcs) {
    if (!npcs.length) {
      npcList.innerHTML =
        '<div class="prod-empty"><strong>还没有 NPC</strong><p>请先到主控台「NPC 管理」创建，然后刷新此页。</p></div>';
      return;
    }
    npcList.innerHTML = npcs
      .map(
        (npc) => `
        <article class="prod-npc-item">
          <div class="prod-npc-main">
            <div class="prod-npc-title">
              <strong>${esc(npc.name)}</strong>
              <span class="prod-npc-id">#${npc.id}</span>
            </div>
            <span class="prod-npc-code">击杀码 ${esc(npc.qr_code || "XXXXXX")}</span>
            <code class="prod-npc-url" title="${esc(npc.url)}">${esc(npc.url)}</code>
          </div>
          <div class="prod-npc-actions">
            <button class="btn btn-primary btn-sm" data-npc-write="${npc.id}" type="button">写入 NPC 标签</button>
            <button class="btn btn-ghost btn-sm" data-npc-copy="${npc.id}" type="button">复制 URL</button>
          </div>
        </article>`
      )
      .join("");
    npcList.querySelectorAll("[data-npc-write]").forEach((button) => {
      const npc = npcs.find((item) => item.id === Number(button.dataset.npcWrite));
      button.addEventListener("click", () => {
        if (npc) writeNpcTag(npc, button);
      });
    });
    npcList.querySelectorAll("[data-npc-copy]").forEach((button) => {
      const npc = npcs.find((item) => item.id === Number(button.dataset.npcCopy));
      button.addEventListener("click", () => {
        if (npc) copyText(npc.url);
      });
    });
  }

  async function openNpcModal() {
    npcModal.hidden = false;
    npcList.innerHTML = '<div class="prod-empty">正在加载 NPC…</div>';
    try {
      const data = await api("/api/production/npcs");
      renderNpcs(data.npcs || []);
    } catch (error) {
      npcList.innerHTML = `<div class="prod-empty"><strong>无法读取 NPC</strong><p>${esc(
        error.message
      )}</p></div>`;
    }
  }

  npcOpen?.addEventListener("click", openNpcModal);
  npcModal?.querySelectorAll("[data-npc-close]").forEach((node) => {
    node.addEventListener("click", () => {
      npcModal.hidden = true;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !npcModal.hidden) npcModal.hidden = true;
  });

  document.getElementById("nfc-retry").addEventListener("click", checkNfc);

  checkNfc();
  setInterval(checkNfc, 15000);
  load();
  setInterval(() => {
    if (!state.busy) load();
  }, 20000);
})();
