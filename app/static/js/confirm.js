(() => {
  const { api, setBusy, toast } = window.RIP;
  const app = document.getElementById("confirm-app");
  if (!app) return;

  const code = app.dataset.code || "";
  const codeError = app.dataset.codeError || "";
  const loading = document.getElementById("target-loading");
  const ready = document.getElementById("target-ready");
  const errorBox = document.getElementById("target-error");
  const errorTitle = document.getElementById("error-title");
  const errorMessage = document.getElementById("error-message");
  const targetCard = document.getElementById("target-card");
  const targetColor = document.getElementById("target-color");
  const targetAvatar = document.getElementById("target-avatar");
  const targetGroup = document.getElementById("target-group");
  const targetName = document.getElementById("target-name");
  const targetStatus = document.getElementById("target-status");
  const selfWarning = document.getElementById("self-warning");
  const actions = document.getElementById("confirm-actions");
  const confirmButton = document.getElementById("confirm-kill");
  const confirmLabel = document.getElementById("confirm-label");
  const success = document.getElementById("kill-success");
  const successTitle = document.getElementById("success-title");
  const successDetail = document.getElementById("success-detail");
  let isSelf = false;
  let canKill = false;

  function showTargetError(title, message) {
    if (loading) loading.hidden = true;
    if (ready) ready.hidden = true;
    if (actions) actions.hidden = true;
    if (selfWarning) selfWarning.hidden = true;
    if (errorBox) errorBox.hidden = false;
    if (errorTitle) errorTitle.textContent = title;
    if (errorMessage) errorMessage.textContent = message;
  }

  function colorizeGroup(group) {
    if (!group) return;
    targetGroup.textContent = "";
    const dot = document.createElement("i");
    targetGroup.appendChild(dot);
    targetGroup.appendChild(document.createTextNode(group.name || "未知组别"));
    targetGroup.style.setProperty("--group-color", group.color || "#888888");
    targetCard.style.setProperty("--group-color", group.color || "#888888");
    targetColor.style.background = group.color || "#888888";
  }

  async function lookup() {
    if (codeError || code.length !== 6) {
      showTargetError("击杀码无效", codeError || "请输入完整的 6 位击杀码");
      return;
    }
    try {
      const data = await api("/api/kills/lookup", {
        method: "POST",
        body: { code },
      });
      const victim = data.victim;
      if (loading) loading.hidden = true;
      if (errorBox) errorBox.hidden = true;
      if (ready) ready.hidden = false;
      colorizeGroup(victim.group);
      targetAvatar.textContent = window.RIP.initials(victim.name);
      targetAvatar.style.color = victim.group?.color || "";
      targetName.textContent = victim.name || "未注册队员";
      targetStatus.textContent = data.can_kill
        ? data.is_self
          ? "可自爆"
          : "存活 · 可击杀"
        : data.message || window.RIP.statusLabel(victim.status);
      targetStatus.classList.toggle("is-dead", !data.can_kill);
      isSelf = Boolean(data.is_self);
      canKill = Boolean(data.can_kill);

      if (isSelf) {
        selfWarning.hidden = false;
        confirmLabel.textContent = "确认自爆";
        confirmButton.classList.remove("btn-danger");
        confirmButton.classList.add("btn-primary");
      } else {
        selfWarning.hidden = true;
        confirmLabel.textContent = "确认淘汰";
      }

      if (canKill) {
        actions.hidden = false;
      } else {
        actions.hidden = true;
        toast(data.message || "该队员当前无法被击杀", "error");
      }
    } catch (err) {
      showTargetError("无法确认目标", err.message);
      if (err.status === 401) {
        window.setTimeout(() => window.location.replace("/"), 1200);
      }
    }
  }

  confirmButton?.addEventListener("click", async () => {
    if (!canKill) return;
    if (isSelf) {
      const ok = window.confirm("确认自爆？你将立即被淘汰，击杀数与个人被击杀数各 +1。");
      if (!ok) return;
    }
    setBusy(confirmButton, true, "正在提交…");
    try {
      const data = await api("/api/kills/confirm", {
        method: "POST",
        body: { code },
      });
      targetCard.hidden = true;
      actions.hidden = true;
      selfWarning.hidden = true;
      success.hidden = false;
      successTitle.textContent = data.message || (data.kind === "self" ? "自爆成功" : "击杀成功");
      successDetail.textContent =
        data.kind === "self"
          ? "你已自爆离场。本轮击杀 +1，被击杀 +1。"
          : "目标已被淘汰，记录已同步至技术台看板。";
      success.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (err) {
      setBusy(confirmButton, false);
      toast(err.message, "error", 4200);
      if (err.status === 409) {
        if (actions) actions.hidden = true;
        if (targetStatus) {
          targetStatus.textContent = err.message;
          targetStatus.classList.add("is-dead");
        }
      } else if (err.status === 401) {
        window.setTimeout(() => window.location.replace("/"), 1200);
      }
    }
  });

  lookup();
})();
