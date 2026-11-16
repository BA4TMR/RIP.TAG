(() => {
  const {
    api,
    toast,
    setBusy,
    showError,
    hideError,
    el,
    initials,
    relativeTime,
    formatDateTime,
    statusLabel,
    statusClass,
    debounce,
  } = window.RIP;

  const app = document.getElementById("console-app");
  if (!app) return;
  const base = app.dataset.base || "";
  const state = {
    roster: [],
    members: [],
    activeTab: "members",
    stream: null,
  };

  const liveStatus = document.getElementById("console-live");
  const groupHost = document.getElementById("console-groups");
  const feedHost = document.getElementById("console-feed");
  const feedCount = document.getElementById("console-feed-count");
  const memberBody = document.querySelector("#member-table tbody");
  const killBody = document.querySelector("#kill-table tbody");
  const scoreBody = document.querySelector("#score-table tbody");
  const auditBody = document.querySelector("#audit-table tbody");
  const npcBody = document.querySelector("#npc-table tbody");
  const npcForm = document.getElementById("npc-create-form");
  const npcName = document.getElementById("npc-name");
  const npcError = document.getElementById("npc-create-error");
  const npcSubmit = document.getElementById("npc-create-submit");
  const npcCreated = document.getElementById("npc-created");
  const npcCreatedName = document.getElementById("npc-created-name");
  const npcCreatedUrl = document.getElementById("npc-created-url");
  const npcCopyCreated = document.getElementById("npc-copy-created");
  let createdNpcUrl = "";
  const memberSearch = document.getElementById("member-search");
  const memberGroupFilter = document.getElementById("member-group-filter");
  const memberStatusFilter = document.getElementById("member-status-filter");
  const killStatusFilter = document.getElementById("kill-status-filter");
  const killScopeFilter = document.getElementById("kill-scope-filter");
  const scoreScopeFilter = document.getElementById("score-scope-filter");
  const auditScopeFilter = document.getElementById("audit-scope-filter");
  const auditActionFilter = document.getElementById("audit-action-filter");
  const manualForm = document.getElementById("manual-kill-form");
  const manualKiller = document.getElementById("manual-kill-killer");
  const manualVictim = document.getElementById("manual-kill-victim");
  const manualNote = document.getElementById("manual-kill-note");
  const manualSubmit = document.getElementById("manual-kill-submit");
  const manualError = document.getElementById("manual-kill-error");
  const scoreForm = document.getElementById("console-score-form");
  const scoreGroup = document.getElementById("console-score-group");
  const scoreDelta = document.getElementById("console-score-delta");
  const scoreReason = document.getElementById("console-score-reason");
  const scoreSubmit = document.getElementById("console-score-submit");
  const scoreError = document.getElementById("console-score-error");
  const resetButton = document.getElementById("reset-btn");
  const resetConfirm = document.getElementById("reset-confirm");
  const resetError = document.getElementById("reset-error");

  function setLive(online) {
    liveStatus?.classList.toggle("is-offline", !online);
    const text = liveStatus?.querySelector("span");
    if (text) text.textContent = online ? "SSE 已连接" : "SSE 重连中";
  }

  function consoleApi(path, options) {
    return api(`${base}${path}`, options);
  }

  function totalNodes(scope = app) {
    return scope.querySelectorAll("[data-total]");
  }

  function applyTotals(totals) {
    totalNodes().forEach((node) => {
      node.textContent = String(totals?.[node.dataset.total] ?? 0);
    });
  }

  function renderGroups(groups) {
    groupHost.replaceChildren();
    groups.forEach((group) => {
      const card = el("article", "console-group-card");
      card.style.setProperty("--group-color", group.color);
      const head = el("div", "console-group-head");
      head.append(el("i"), el("span", "", group.name), el("span", "console-group-score", `${group.score >= 0 ? "+" : ""}${group.score} 分`));
      const nums = el("div", "console-group-nums");
      const alive = el("div", "console-group-alive");
      alive.append(el("strong", "", String(group.alive)), el("span", "", `/ ${group.total} 存活`));
      const side = el("div", "console-group-side");
      side.append(
        el("span", "", `击杀 ${group.kills}`),
        el("span", "", `被击杀 ${group.deaths}`),
        el("strong", "", `${group.eliminated} 淘汰 · ${group.unregistered} 未注册`),
      );
      nums.append(alive, side);
      const meter = el("div", "console-group-meter");
      const fill = el("i");
      fill.style.width = `${group.total ? Math.round((group.alive / group.total) * 100) : 0}%`;
      meter.appendChild(fill);
      card.append(head, nums, meter);
      groupHost.appendChild(card);
    });
  }

  function renderFeed(kills) {
    if (!kills?.length) {
      feedHost.replaceChildren(el("div", "empty-state", "等待击杀记录"));
      feedCount.textContent = "0";
      return;
    }
    feedHost.replaceChildren();
    kills.forEach((kill) => {
      const item = el("div", "feed-item");
      const avatar = el("div", "feed-avatar", initials(kill.killer.name));
      avatar.style.setProperty("--feed-color", kill.killer.color);
      const copy = el("div", "feed-copy");
      const line = el("strong", "");
      line.textContent = `${kill.killer.name || "?"} → ${kill.victim.name || "?"}`;
      const metaLine = el("small", "");
      metaLine.textContent = `${kill.killer.group || "?"} → ${kill.victim.group || "?"}`;
      copy.append(line, metaLine);
      const meta = el("div", "feed-meta");
      meta.append(el("div", "", relativeTime(kill.created_at)));
      if (kill.kind === "self") meta.append(el("div", "feed-self", "自爆"));
      else if (kill.source === "manual") meta.append(el("div", "feed-self", "补录"));
      item.append(avatar, copy, meta);
      feedHost.appendChild(item);
    });
    feedCount.textContent = String(kills.length);
  }

  function applyDashboard(data) {
    applyTotals(data.totals);
    renderGroups(data.groups || []);
    renderFeed(data.recent_kills || []);
    document.getElementById("console-round").textContent = data.round?.name || "—";
  }

  async function loadDashboard() {
    try {
      const data = await consoleApi("/api/dashboard");
      applyDashboard(data);
      setLive(true);
    } catch (err) {
      setLive(false);
      if (err.status === 401 || err.status === 403) toast(err.message, "error", 5000);
    }
  }

  function connectStream() {
    if (!window.EventSource) {
      setLive(false);
      return;
    }
    state.stream?.close();
    state.stream = new EventSource(`${base}/api/stream`);
    state.stream.addEventListener("open", () => setLive(true));
    state.stream.addEventListener("dashboard", (event) => {
      try {
        applyDashboard(JSON.parse(event.data));
        setLive(true);
      } catch {
        // Ignore malformed frames.
      }
    });
    state.stream.addEventListener("error", () => setLive(false));
  }

  function emptyRow(tbody, columns, message = "暂无数据") {
    const tr = document.createElement("tr");
    const td = el("td", "table-empty", message);
    td.colSpan = columns;
    tr.appendChild(td);
    tbody.replaceChildren(tr);
  }

  function personCell(name, id, color, groupName) {
    const cell = el("div", "cell-person");
    const avatar = el("div", "cell-avatar", initials(name));
    avatar.style.setProperty("--cell-color", color || "#888888");
    const copy = el("div");
    copy.append(el("strong", "", name || "未登记"), el("small", "", `${groupName || "?"} · ${id.slice(-6).toUpperCase()}`));
    cell.append(avatar, copy);
    return cell;
  }

  function groupTag(name, color) {
    const tag = el("span", "group-tag", name || "?");
    tag.style.setProperty("--group-color", color || "#888888");
    return tag;
  }

  async function loadRoster() {
    try {
      const data = await consoleApi("/api/members?limit=300");
      state.roster = data.members || [];
      populateManualSelects();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function populateManualSelects() {
    if (!manualKiller || !manualVictim) return;
    const active = state.roster
      .filter((member) => member.status === "active" && member.name)
      .sort((a, b) => a.group.name.localeCompare(b.group.name, "zh-CN") || a.name.localeCompare(b.name, "zh-CN"));
    const currentKiller = manualKiller.value;
    const currentVictim = manualVictim.value;
    const placeholderKiller = new Option("选择击杀者", "");
    const placeholderVictim = new Option("选择被击杀者", "");
    const options = active.map((member) => {
      const label = `${member.group.name} / ${member.name} · ${member.id.slice(-4).toUpperCase()}`;
      return new Option(label, member.id);
    });
    manualKiller.replaceChildren(placeholderKiller, ...options.map((option) => option.cloneNode(true)));
    manualVictim.replaceChildren(placeholderVictim, ...options);
    if (active.some((member) => member.id === currentKiller)) manualKiller.value = currentKiller;
    if (active.some((member) => member.id === currentVictim)) manualVictim.value = currentVictim;
    const disabled = active.length < 2;
    manualKiller.disabled = disabled;
    manualVictim.disabled = disabled;
    if (manualSubmit) manualSubmit.disabled = disabled;
  }

  async function loadMembers() {
    const params = new URLSearchParams();
    if (memberSearch?.value.trim()) params.set("q", memberSearch.value.trim());
    if (memberGroupFilter?.value) params.set("group_id", memberGroupFilter.value);
    if (memberStatusFilter?.value && memberStatusFilter.value !== "all") params.set("status", memberStatusFilter.value);
    params.set("limit", "300");
    try {
      const data = await consoleApi(`/api/members?${params.toString()}`);
      state.members = data.members || [];
      renderMemberTable();
    } catch (err) {
      emptyRow(memberBody, 6, err.message);
    }
  }

  function renderMemberTable() {
    if (!state.members.length) {
      emptyRow(memberBody, 6, "没有符合条件的队员");
      return;
    }
    const rows = state.members.map((member) => {
      const tr = document.createElement("tr");
      const person = document.createElement("td");
      person.appendChild(personCell(member.name, member.id, member.group?.color, member.group?.name));

      const group = document.createElement("td");
      group.appendChild(groupTag(member.group?.name, member.group?.color));

      const status = document.createElement("td");
      status.appendChild(el("span", `state-pill ${statusClass(member.status)}`, statusLabel(member.status)));

      const code = el("td", "code-cell", member.qr_code || "—");
      const registered = el("td", "time-cell", member.registered_at ? formatDateTime(member.registered_at) : "—");
      const actions = el("td", "align-right");
      const wrap = el("div", "row-actions");

      if (member.status === "eliminated") {
        const revive = el("button", "row-btn", "复活");
        revive.type = "button";
        revive.addEventListener("click", () => reviveMember(member));
        wrap.appendChild(revive);
      }
      const rename = el("button", "row-btn", member.status === "unregistered" ? "登记姓名" : "改名");
      rename.type = "button";
      rename.addEventListener("click", () => renameMember(member));
      wrap.appendChild(rename);
      actions.appendChild(wrap);
      tr.append(person, group, status, code, registered, actions);
      return tr;
    });
    memberBody.replaceChildren(...rows);
  }

  async function reviveMember(member) {
    if (!window.confirm(`确认复活「${member.name || "未登记"}」？历史击杀记录不会回滚。`)) return;
    try {
      await consoleApi(`/api/members/${encodeURIComponent(member.id)}/revive`, { method: "POST" });
      toast(`${member.name || "队员"} 已复活`, "success");
      await refreshAfterChange("members");
    } catch (err) {
      toast(err.message, "error", 4200);
    }
  }

  async function renameMember(member) {
    const current = member.name || "";
    const next = window.prompt(
      member.status === "unregistered" ? "为该队员登记姓名（将标记为存活）：" : "修改姓名（会写入审计）：",
      current,
    );
    if (next === null) return;
    const name = next.trim();
    if (!name) {
      toast("姓名不能为空", "error");
      return;
    }
    try {
      await consoleApi(`/api/members/${encodeURIComponent(member.id)}/name`, {
        method: "POST",
        body: { name },
      });
      toast("姓名已更新", "success");
      await refreshAfterChange("members");
    } catch (err) {
      toast(err.message, "error", 4200);
    }
  }

  async function loadKills() {
    const params = new URLSearchParams({
      status: killStatusFilter?.value || "active",
      scope: killScopeFilter?.value || "current",
      limit: "200",
    });
    try {
      const data = await consoleApi(`/api/kills?${params.toString()}`);
      renderKillTable(data.kills || []);
    } catch (err) {
      emptyRow(killBody, 7, err.message);
    }
  }

  function renderKillTable(kills) {
    if (!kills.length) {
      emptyRow(killBody, 7, "暂无击杀记录");
      return;
    }
    const rows = kills.map((kill) => {
      const tr = document.createElement("tr");
      tr.append(el("td", "time-cell", formatDateTime(kill.created_at)));
      const killer = document.createElement("td");
      killer.appendChild(personCell(kill.killer_name, kill.killer_id, kill.killer_color, kill.killer_group));
      const victim = document.createElement("td");
      victim.appendChild(personCell(kill.victim_name, kill.victim_id, kill.victim_color, kill.victim_group));
      tr.append(killer, victim);
      tr.append(el("td", "", kill.kind === "self" ? "自爆" : "正常击杀"));
      tr.append(el("td", "", kill.source === "manual" ? "技术台补录" : "扫码/手输"));
      const status = document.createElement("td");
      status.appendChild(el("span", `state-pill ${kill.status === "active" ? "state-active" : "state-revoked"}`, kill.status === "active" ? "有效" : "已撤销"));
      tr.appendChild(status);
      const actions = el("td", "align-right");
      const wrap = el("div", "row-actions");
      if (kill.status === "active") {
        const revoke = el("button", "row-btn is-danger", "撤销");
        revoke.type = "button";
        revoke.addEventListener("click", () => revokeKill(kill));
        wrap.appendChild(revoke);
      } else {
        wrap.appendChild(el("span", "state-pill state-revoked", kill.revoked_by || "已撤销"));
      }
      actions.appendChild(wrap);
      tr.appendChild(actions);
      return tr;
    });
    killBody.replaceChildren(...rows);
  }

  async function revokeKill(kill) {
    const message = `确认撤销「${kill.killer_name || "?"} → ${kill.victim_name || "?"}」这次击杀？统计将回滚，被击杀者将自动复活。`;
    if (!window.confirm(message)) return;
    try {
      const data = await consoleApi(`/api/kills/${kill.id}/revoke`, { method: "POST" });
      toast(data.revived ? "击杀已撤销，被击杀者已复活" : "击杀已撤销", "success", 4200);
      await refreshAfterChange("kills");
    } catch (err) {
      toast(err.message, "error", 4200);
    }
  }

  async function loadScores() {
    const scope = scoreScopeFilter?.value || "current";
    try {
      const data = await consoleApi(`/api/scores?scope=${encodeURIComponent(scope)}&limit=300`);
      renderScoreTable(data.adjustments || []);
    } catch (err) {
      emptyRow(scoreBody, 7, err.message);
    }
  }

  function renderScoreTable(scores) {
    if (!scores.length) {
      emptyRow(scoreBody, 7, "暂无积分记录");
      return;
    }
    const rows = scores.map((item) => {
      const tr = document.createElement("tr");
      tr.append(el("td", "time-cell", formatDateTime(item.created_at)));
      const group = document.createElement("td");
      group.appendChild(groupTag(item.group_name, item.group_color));
      tr.appendChild(group);
      const delta = el("td", `score-delta${item.delta < 0 ? " is-negative" : ""}${item.status === "revoked" ? " is-revoked" : ""}`, `${item.delta > 0 ? "+" : ""}${item.delta}`);
      tr.appendChild(delta);
      tr.append(el("td", "", item.reason || "—"));
      tr.append(el("td", "", item.operator_role === "admin" ? "技术台" : item.operator_role === "npc" ? `NPC #${item.operator_id}` : item.operator_role));
      const status = document.createElement("td");
      status.appendChild(el("span", `state-pill ${item.status === "active" ? "state-active" : "state-revoked"}`, item.status === "active" ? "有效" : "已撤销"));
      tr.appendChild(status);
      const actions = el("td", "align-right");
      const wrap = el("div", "row-actions");
      if (item.status === "active") {
        const revoke = el("button", "row-btn is-danger", "撤销");
        revoke.type = "button";
        revoke.addEventListener("click", () => revokeScore(item));
        wrap.appendChild(revoke);
      }
      actions.appendChild(wrap);
      tr.appendChild(actions);
      return tr;
    });
    scoreBody.replaceChildren(...rows);
  }

  async function revokeScore(item) {
    if (!window.confirm(`确认撤销「${item.group_name} ${item.delta > 0 ? "+" : ""}${item.delta}」这条积分记录？`)) return;
    try {
      await consoleApi(`/api/score/${item.id}/revoke`, { method: "POST" });
      toast("积分记录已撤销", "success");
      await refreshAfterChange("scores");
    } catch (err) {
      toast(err.message, "error", 4200);
    }
  }

  async function loadAudit() {
    const params = new URLSearchParams({
      scope: auditScopeFilter?.value || "all",
      limit: "200",
    });
    if (auditActionFilter?.value) params.set("action", auditActionFilter.value);
    try {
      const data = await consoleApi(`/api/audit?${params.toString()}`);
      renderAuditTable(data.logs || []);
    } catch (err) {
      emptyRow(auditBody, 5, err.message);
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制", "success");
    } catch {
      window.prompt("复制 NPC URL：", text);
    }
  }

  async function loadNpcs() {
    try {
      const data = await consoleApi("/api/npcs");
      renderNpcTable(data.npcs || []);
    } catch (err) {
      emptyRow(npcBody, 5, err.message);
    }
  }

  function renderNpcTable(npcs) {
    if (!npcs.length) {
      emptyRow(npcBody, 5, "还没有 NPC，先在左侧创建");
      return;
    }
    const rows = npcs.map((npc) => {
      const tr = document.createElement("tr");
      const identity = document.createElement("td");
      const name = el("strong", "", npc.name);
      identity.append(name, el("div", "table-subline", `NPC #${npc.id}`));
      tr.appendChild(identity);
      tr.append(el("td", "mono-cell", npc.qr_code || "XXXXXX"));
      const stateCell = document.createElement("td");
      stateCell.appendChild(
        el(
          "span",
          `state-pill ${npc.active ? "state-active" : "state-revoked"}`,
          npc.active ? "启用" : "已停用",
        ),
      );
      tr.appendChild(stateCell);
      const urlCell = el("td", "npc-url-cell", npc.url);
      urlCell.title = npc.url;
      tr.appendChild(urlCell);

      const actions = document.createElement("td");
      actions.className = "align-right";
      const copy = el("button", "btn btn-small btn-ghost", "复制 URL");
      copy.type = "button";
      copy.addEventListener("click", () => copyText(npc.url));
      actions.appendChild(copy);
      if (npc.active) {
        const disable = el("button", "btn btn-small btn-danger", "停用");
        disable.type = "button";
        disable.addEventListener("click", async () => {
          if (!window.confirm(`停用 NPC「${npc.name}」？已登录的场务会立即失效。`)) return;
          disable.disabled = true;
          try {
            await consoleApi(`/api/npcs/${npc.id}/disable`, { method: "POST" });
            toast(`NPC「${npc.name}」已停用`, "success");
            await loadNpcs();
          } catch (err) {
            toast(err.message, "error");
            disable.disabled = false;
          }
        });
        actions.appendChild(disable);
      }
      tr.appendChild(actions);
      return tr;
    });
    npcBody.replaceChildren(...rows);
  }

  npcForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError(npcError);
    const name = npcName.value.trim();
    if (!name) {
      showError(npcError, "请输入 NPC 名称");
      npcName.focus();
      return;
    }
    setBusy(npcSubmit, true, "创建中…");
    try {
      const data = await consoleApi("/api/npcs", {
        method: "POST",
        body: { name },
      });
      const npc = data.npc;
      createdNpcUrl = npc.url;
      npcCreated.hidden = false;
      npcCreatedName.textContent = `${npc.name} · 击杀码 ${npc.qr_code}`;
      npcCreatedUrl.textContent = npc.url;
      npcName.value = "";
      toast(`NPC「${npc.name}」已创建`, "success");
      await loadNpcs();
    } catch (err) {
      showError(npcError, err.message);
    } finally {
      setBusy(npcSubmit, false);
    }
  });

  npcCopyCreated?.addEventListener("click", () => {
    if (createdNpcUrl) copyText(createdNpcUrl);
  });
  document.getElementById("npc-refresh")?.addEventListener("click", loadNpcs);

  const actionLabels = {
    kill_recorded: "击杀记录",
    kill_self: "自爆",
    kill_revoked: "撤销击杀",
    member_revived: "复活队员",
    member_name_changed: "修改姓名",
    member_registered: "队员注册",
    npc_registered_member: "NPC 补注册",
    npc_created: "创建 NPC",
    npc_disabled: "停用 NPC",
    score_added: "新增积分",
    score_revoked: "撤销积分",
    activity_reset_soft: "软重置",
    activity_reset_hard: "硬重置",
    export_created: "导出数据",
    production_written: "制作-已写卡",
    production_printed: "制作-已打印",
    production_done: "制作-已完成",
    production_error: "制作-失败",
    production_reset: "制作-重置",
  };

  function renderAuditTable(logs) {
    if (!logs.length) {
      emptyRow(auditBody, 5, "暂无审计日志");
      return;
    }
    const rows = logs.map((log) => {
      const tr = document.createElement("tr");
      tr.append(el("td", "time-cell", formatDateTime(log.created_at)));
      const actor = `${log.actor_role === "admin" ? "技术台" : log.actor_role === "npc" ? "NPC" : "队员"}${log.actor_id ? ` · ${String(log.actor_id).slice(-5)}` : ""}`;
      tr.append(el("td", "", actor));
      tr.append(el("td", "", actionLabels[log.action] || log.action));
      tr.append(el("td", "", `${log.target_type || "—"}${log.target_id ? ` #${String(log.target_id).slice(-6)}` : ""}`));
      const payload = log.payload ? JSON.stringify(log.payload) : "";
      const detail = el("td", "audit-payload", payload || "—");
      if (payload) detail.title = payload;
      tr.appendChild(detail);
      return tr;
    });
    auditBody.replaceChildren(...rows);
  }

  function switchTab(name) {
    state.activeTab = name;
    app.querySelectorAll(".console-tabs .tab-btn").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.tab === name);
    });
    app.querySelectorAll(".console-panel").forEach((panel) => {
      panel.hidden = panel.id !== `panel-${name}`;
    });
    refreshCurrentTab();
  }

  function refreshCurrentTab() {
    const actions = {
      members: loadMembers,
      kills: loadKills,
      scores: loadScores,
      npcs: loadNpcs,
      audit: loadAudit,
      system: loadRoster,
    };
    actions[state.activeTab]?.();
  }

  async function refreshAfterChange(tab = state.activeTab) {
    state.activeTab = tab;
    await Promise.all([loadDashboard(), loadRoster(), refreshCurrentTab()]);
  }

  app.querySelectorAll(".console-tabs .tab-btn").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });

  app.querySelectorAll("[data-goto-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      switchTab(button.dataset.gotoTab);
      if (button.dataset.focus) {
        window.setTimeout(() => document.getElementById(button.dataset.focus)?.focus(), 50);
      }
    });
  });

  memberSearch?.addEventListener(
    "input",
    debounce(loadMembers, 220),
  );
  memberGroupFilter?.addEventListener("change", loadMembers);
  memberStatusFilter?.addEventListener("change", loadMembers);
  killStatusFilter?.addEventListener("change", loadKills);
  killScopeFilter?.addEventListener("change", loadKills);
  scoreScopeFilter?.addEventListener("change", loadScores);
  auditScopeFilter?.addEventListener("change", loadAudit);
  auditActionFilter?.addEventListener("change", loadAudit);
  document.getElementById("member-refresh")?.addEventListener("click", loadMembers);
  document.getElementById("kill-refresh")?.addEventListener("click", loadKills);
  document.getElementById("score-refresh")?.addEventListener("click", loadScores);
  document.getElementById("audit-refresh")?.addEventListener("click", loadAudit);

  manualForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError(manualError);
    const killerId = manualKiller.value;
    const victimId = manualVictim.value;
    if (!killerId || !victimId) {
      showError(manualError, "请选择击杀者与被击杀者");
      return;
    }
    if (killerId === victimId) {
      showError(manualError, "击杀者与被击杀者不能是同一人");
      return;
    }
    if (!window.confirm("确认补录这次击杀？该操作会立即淘汰被击杀者并写入审计。")) return;
    setBusy(manualSubmit, true, "正在补录…");
    try {
      await consoleApi("/api/kills/manual", {
        method: "POST",
        body: { killer_id: killerId, victim_id: victimId, note: manualNote.value.trim() || null },
      });
      toast("手动击杀已补录", "success");
      manualForm.reset();
      await refreshAfterChange("system");
    } catch (err) {
      showError(manualError, err.message);
    } finally {
      setBusy(manualSubmit, false);
    }
  });

  scoreForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError(scoreError);
    const delta = Number(scoreDelta.value);
    if (!Number.isInteger(delta) || delta === 0 || Math.abs(delta) > 1000) {
      showError(scoreError, "请输入 -1000 到 1000 之间的非零整数");
      return;
    }
    setBusy(scoreSubmit, true, "正在提交…");
    try {
      await consoleApi("/api/score", {
        method: "POST",
        body: {
          group_id: Number(scoreGroup.value),
          delta,
          reason: scoreReason.value.trim() || null,
        },
      });
      toast("积分修正已记录", "success");
      scoreReason.value = "";
      await refreshAfterChange("scores");
    } catch (err) {
      showError(scoreError, err.message);
    } finally {
      setBusy(scoreSubmit, false);
    }
  });

  async function downloadExport(type) {
    const scope = document.getElementById("export-all-scope")?.checked ? "all" : "current";
    const url = `${base}/api/export?type=${encodeURIComponent(type)}&scope=${scope}`;
    try {
      const response = await fetch(url, { credentials: "same-origin", headers: { Accept: "text/csv" } });
      if (!response.ok) {
        let message = "导出失败";
        try {
          const payload = await response.json();
          message = payload?.error?.message || message;
        } catch {
          // non-JSON error
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="([^"]+)"/);
      const filename = match?.[1] || `activity-${type}.csv`;
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 2000);
      toast("CSV 已开始下载", "success");
    } catch (err) {
      toast(err.message, "error", 4200);
    }
  }

  app.querySelectorAll("[data-export]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      downloadExport(link.dataset.export);
    });
  });

  resetButton?.addEventListener("click", async () => {
    hideError(resetError);
    const mode = app.querySelector('input[name="reset-mode"]:checked')?.value || "soft";
    const confirmText = resetConfirm.value.trim().toUpperCase();
    if (confirmText !== "RESET") {
      showError(resetError, "请输入 RESET 以确认重置");
      return;
    }
    const modeLabel = mode === "hard" ? "硬清空所有姓名" : "软重置并开启新一轮";
    if (!window.confirm(`确认${modeLabel}？此操作不可撤销，请确保已完成备份。`)) return;
    setBusy(resetButton, true, "正在重置…");
    try {
      const result = await consoleApi("/api/reset", {
        method: "POST",
        body: { mode, confirm: confirmText },
      });
      toast(`已${result.mode === "hard" ? "硬重置" : "软重置"}，新轮次：${result.round_name}`, "success", 5000);
      resetConfirm.value = "";
      await Promise.all([loadDashboard(), loadRoster(), loadMembers()]);
    } catch (err) {
      showError(resetError, err.message);
    } finally {
      setBusy(resetButton, false);
    }
  });

  loadDashboard();
  loadRoster();
  loadMembers();
  connectStream();
  window.setInterval(loadDashboard, 30000);
  window.addEventListener("pagehide", () => state.stream?.close());
})();
