(() => {
  const { api, toast, setBusy } = window.RIP;
  const app = document.getElementById("home-app");
  if (!app) return;

  const statusNode = document.getElementById("member-status");
  const killsNode = document.getElementById("stat-kills");
  const deathsNode = document.getElementById("stat-deaths");
  const scoreNode = document.getElementById("stat-score");
  const logout = document.getElementById("logout-btn");
  let currentStatus = app.dataset.status;
  let timer = null;
  let stopped = false;

  function applyStatus(status) {
    if (!statusNode) return;
    statusNode.className = `status-badge status-${status}`;
    statusNode.innerHTML = '<span class="status-dot"></span>';
    statusNode.appendChild(
      document.createTextNode(
        { active: "存活中", eliminated: "已淘汰", unregistered: "未注册" }[status] || status,
      ),
    );
  }

  async function refresh() {
    if (stopped || document.hidden) return;
    try {
      const data = await api("/api/me");
      killsNode.textContent = String(data.stats.kills);
      deathsNode.textContent = String(data.stats.deaths);
      scoreNode.textContent = String(data.stats.group_score);
      if (data.member.status !== currentStatus) {
        currentStatus = data.member.status;
        if (currentStatus === "eliminated") {
          toast("你已被淘汰，名牌已失效", "error", 1800);
          window.setTimeout(() => window.location.reload(), 1100);
        } else {
          window.location.reload();
        }
      }
    } catch (err) {
      if (err.status === 401) {
        stopped = true;
        window.location.replace("/");
      }
    }
  }

  logout?.addEventListener("click", async () => {
    if (!window.confirm("确定退出当前队员登录？")) return;
    setBusy(logout, true);
    try {
      await api("/api/logout", { method: "POST" });
    } catch {
      // Clear the page anyway; a stale cookie is harmless.
    } finally {
      stopped = true;
      window.location.replace("/");
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });

  if (currentStatus === "active") {
    timer = window.setInterval(refresh, 12000);
    window.addEventListener("pagehide", () => window.clearInterval(timer), { once: true });
    refresh();
  }
})();
