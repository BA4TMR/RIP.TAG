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
    statusLabel,
    statusClass,
    debounce,
  } = window.RIP;
  const app = document.getElementById("staff-app");
  if (!app) return;

  const liveStatus = document.getElementById("live-status");
  const groupsHost = document.getElementById("staff-groups");
  const feedHost = document.getElementById("staff-feed");
  const feedCount = document.getElementById("feed-count");
  const unregisteredCount = document.getElementById("unregistered-count");
  const assistList = document.getElementById("assist-list");
  const memberSearch = document.getElementById("member-search");
  const scoreFeed = document.getElementById("score-feed");
  const scoreForm = document.getElementById("npc-score-form");
  const scoreError = document.getElementById("score-error");
  const scoreSubmit = document.getElementById("score-submit");
  const scoreDelta = document.getElementById("score-delta");
  const scoreReason = document.getElementById("score-reason");
  const scoreGroup = document.getElementById("score-group");
  let members = [];
  let stream = null;
  let fallbackTimer = null;

  function setLive(online) {
    liveStatus?.classList.toggle("is-offline", !online);
    const text = liveStatus?.querySelector("span");
    if (text) text.textContent = online ? "实时连接中" : "连接断开，重试中";
  }

  function renderGroups(groups) {
    if (!groupsHost) return;
    groupsHost.replaceChildren();
    groups.forEach((group) => {
      const card = el("article", "group-card");
      card.style.setProperty("--group-color", group.color);
      const head = el("div", "group-card-head");
      head.append(el("i"), el("strong", "", group.name), el("span", "", `#${group.id}`));
      const main = el("div", "group-card-main");
      const alive = el("div", "group-alive");
      alive.append(el("strong", "", String(group.alive)), el("span", "", `/ ${group.total} 存活`));
      const small = el("div", "group-small-stats");
      const kill = el("div");
      kill.append(el("strong", "", String(group.kills)), el("span", "", "击杀"));
      const death = el("div");
      death.append(el("strong", "", String(group.deaths)), el("span", "", "被击杀"));
      const score = el("div");
      score.append(el("strong", "", String(group.score)), el("span", "", "积分"));
      small.append(kill, death, score);
      main.append(alive, small);
      const meter = el("div", "group-meter");
      const fill = el("i");
      fill.style.width = `${group.total ? Math.round((group.alive / group.total) * 100) : 0}%`;
      meter.appendChild(fill);
      card.append(head, main, meter);
      groupsHost.appendChild(card);
    });
  }

  function renderFeed(kills) {
    if (!feedHost) return;
    if (!kills || !kills.length) {
      feedHost.replaceChildren(el("div", "empty-state", "等待第一条击杀记录"));
      if (feedCount) feedCount.textContent = "0";
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
      const small = el("small", "");
      small.textContent = `${kill.killer.group || "?"} → ${kill.victim.group || "?"}`;
      copy.append(line, small);
      const meta = el("div", "feed-meta");
      meta.append(el("div", "", relativeTime(kill.created_at)));
      if (kill.kind === "self") meta.append(el("div", "feed-self", "自爆"));
      else if (kill.source === "manual") meta.append(el("div", "feed-self", "补录"));
      item.append(avatar, copy, meta);
      feedHost.appendChild(item);
    });
    if (feedCount) feedCount.textContent = String(kills.length);
  }

  function applyDashboard(data) {
    app.querySelectorAll("[data-total]").forEach((node) => {
      const key = node.dataset.total;
      node.textContent = key && data.totals ? String(data.totals[key] ?? 0) : "—";
    });
    renderGroups(data.groups || []);
    renderFeed(data.recent_kills || []);
    if (unregisteredCount && data.totals) {
      unregisteredCount.textContent = String(data.totals.unregistered);
    }
  }

  async function loadDashboard() {
    try {
      const data = await api("/api/dashboard");
      applyDashboard(data);
      setLive(true);
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        window.location.replace("/staff");
        return;
      }
      setLive(false);
    }
  }

  function connectStream() {
    if (!window.EventSource) {
      setLive(false);
      return;
    }
    stream?.close();
    stream = new EventSource("/api/stream");
    stream.addEventListener("open", () => setLive(true));
    stream.addEventListener("dashboard", (event) => {
      try {
        applyDashboard(JSON.parse(event.data));
        setLive(true);
      } catch {
        // Ignore malformed frames; the fallback poll still applies.
      }
    });
    stream.addEventListener("error", () => setLive(false));
  }

  function memberRow(member) {
    const row = el("div", "assist-row");
    row.style.setProperty("--group-color", member.group?.color || "#888");
    row.append(el("span", "assist-group"));
    const main = el("div", "assist-main");
    const name = member.name || "未登记";
    main.append(el("strong", "", name));
    const meta = el("span", "");
    const identity = member.id
      ? member.id.slice(-6).toUpperCase()
      : `TASK-${String(member.task_id).padStart(3, "0")}`;
    meta.textContent = `${member.group?.name || "?"} · ${identity}`;
    main.appendChild(meta);
    row.appendChild(main);

    if (member.status === "unregistered") {
      const form = el("form", "assist-form");
      const input = document.createElement("input");
      input.type = "text";
      input.name = "name";
      input.maxLength = 30;
      input.placeholder = "输入真实姓名";
      input.autocomplete = "name";
      input.required = true;
      const button = el("button", "btn btn-small btn-primary", "补注册");
      button.type = "submit";
      form.append(input, button);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = input.value.trim();
        if (!name) {
          input.focus();
          return;
        }
        setBusy(button, true, "提交中");
        try {
          await api("/api/npc/register", {
            method: "POST",
            body: { task_id: member.task_id, name },
          });
          toast(`${name} 已补注册成功`, "success");
          await loadMembers(true);
          await loadDashboard();
        } catch (err) {
          toast(err.message, "error", 4200);
          input.focus();
        } finally {
          setBusy(button, false);
        }
      });
      row.appendChild(form);
    } else {
      const state = el("span", `state-pill ${statusClass(member.status)}`, statusLabel(member.status));
      row.appendChild(state);
    }
    return row;
  }

  function renderMembers(filter = "") {
    if (!assistList) return;
    const needle = filter.trim().toLowerCase();
    const visible = members
      .filter((member) => {
        if (!needle) return true;
        return [member.name, member.id, member.task_id, member.group?.name]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(needle));
      })
      .sort((a, b) => {
        const weight = { unregistered: 0, active: 1, eliminated: 2 };
        return (weight[a.status] ?? 3) - (weight[b.status] ?? 3) ||
          String(a.group?.name).localeCompare(String(b.group?.name), "zh-CN") ||
          String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
      });

    if (!visible.length) {
      assistList.replaceChildren(el("div", "empty-state", "没有符合条件的队员"));
      return;
    }
    assistList.replaceChildren(...visible.map(memberRow));
  }

  async function loadMembers(force = false) {
    if (members.length && !force) {
      renderMembers(memberSearch?.value || "");
      return;
    }
    try {
      const data = await api("/api/npc/members");
      members = data.members || [];
      if (unregisteredCount) {
        unregisteredCount.textContent = String(
          members.filter((member) => member.status === "unregistered").length,
        );
      }
      renderMembers(memberSearch?.value || "");
    } catch (err) {
      if (assistList) assistList.replaceChildren(el("div", "empty-state", err.message));
    }
  }

  function scoreRow(item) {
    const row = el("div", "score-row");
    const delta = el(
      "div",
      `score-delta${item.delta < 0 ? " is-negative" : ""}${item.status === "revoked" ? " is-revoked" : ""}`,
      `${item.delta > 0 ? "+" : ""}${item.delta}`,
    );
    const main = el("div", "score-row-main");
    main.append(el("strong", "", item.group_name || "未知组别"), el("span", "", item.reason || "未填写原因"));
    const time = el("time", "", relativeTime(item.created_at));
    row.append(delta, main, time);
    return row;
  }

  async function loadScores() {
    if (!scoreFeed) return;
    try {
      const data = await api("/api/npc/scores");
      const items = data.adjustments || [];
      if (!items.length) {
        scoreFeed.replaceChildren(el("div", "empty-state", "本轮暂无加分"));
        return;
      }
      scoreFeed.replaceChildren(...items.map(scoreRow));
    } catch (err) {
      scoreFeed.replaceChildren(el("div", "empty-state", err.message));
    }
  }

  function switchTab(name) {
    app.querySelectorAll(".tab-btn[data-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.tab === name);
    });
    app.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.hidden = panel.id !== `panel-${name}`;
    });
    if (name === "register") loadMembers();
    if (name === "score") loadScores();
  }

  app.querySelectorAll(".tab-btn[data-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });

  memberSearch?.addEventListener(
    "input",
    debounce(() => renderMembers(memberSearch.value), 180),
  );

  document.querySelectorAll("#quick-points button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("#quick-points button").forEach((item) => item.classList.remove("is-selected"));
      button.classList.add("is-selected");
      if (scoreDelta) scoreDelta.value = button.dataset.points;
    });
  });

  scoreForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError(scoreError);
    const delta = Number(scoreDelta.value);
    if (!Number.isInteger(delta) || delta < 1 || delta > 1000) {
      showError(scoreError, "请输入 1-1000 的整数分值");
      return;
    }
    setBusy(scoreSubmit, true, "正在提交…");
    try {
      await api("/api/npc/score", {
        method: "POST",
        body: {
          group_id: Number(scoreGroup.value),
          delta,
          reason: scoreReason.value.trim() || null,
        },
      });
      toast("加分已记录并同步看板", "success");
      scoreReason.value = "";
      await Promise.all([loadScores(), loadDashboard()]);
    } catch (err) {
      showError(scoreError, err.message);
    } finally {
      setBusy(scoreSubmit, false);
    }
  });

  document.getElementById("staff-logout")?.addEventListener("click", async () => {
    if (!window.confirm("确定退出 NPC 场务台？")) return;
    try {
      await api("/api/npc/logout", { method: "POST" });
    } catch {
      // Regardless of response, leave the console.
    }
    window.location.replace("/");
  });

  loadDashboard();
  connectStream();
  fallbackTimer = window.setInterval(loadDashboard, 30000);
  window.addEventListener("pagehide", () => {
    window.clearInterval(fallbackTimer);
    stream?.close();
  });
})();
