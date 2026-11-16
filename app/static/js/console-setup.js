(() => {
  "use strict";

  const app = document.getElementById("setup-app");
  if (!app) return;

  const base = app.dataset.base || "";
  const form = document.getElementById("setup-form");
  const groupCount = document.getElementById("group-count");
  const perGroup = document.getElementById("per-group");
  const total = document.getElementById("setup-total");
  const groups = document.getElementById("setup-groups");
  const errorBox = document.getElementById("setup-error");
  const submit = document.getElementById("setup-submit");
  const success = document.getElementById("setup-success");
  const result = document.getElementById("setup-result");

  const previews = groups ? Array.from(groups.querySelectorAll(".group-preview")) : [];

  function updatePreview() {
    const count = Number(groupCount.value);
    const size = Number(perGroup.value);
    total.textContent = String(count * size);
    previews.forEach((node, index) => {
      node.hidden = index >= count;
    });
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
    errorBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  groupCount.addEventListener("change", updatePreview);
  perGroup.addEventListener("change", updatePreview);
  updatePreview();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    const count = Number(groupCount.value);
    const size = Number(perGroup.value);
    if (!Number.isInteger(count) || !Number.isInteger(size)) {
      showError("请选择有效的组数和人数。");
      return;
    }
    if (count * size > 60) {
      showError("总人数不能超过 60。");
      return;
    }

    submit.disabled = true;
    submit.classList.add("is-loading");
    try {
      const response = await fetch(`${base}/api/setup`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_count: count, per_group: size }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 409) {
          showError("活动已经初始化，不能重复生成。请刷新主控台。");
          return;
        }
        showError(payload.error || payload.detail || "生成失败，请检查服务日志。");
        return;
      }
      form.closest(".setup-grid").hidden = true;
      success.hidden = false;
      result.textContent = `已生成 ${payload.group_count} 个组、${payload.task_count} 条制作任务。`;
      success.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch (error) {
      showError("无法连接后端，请检查服务或客户端证书。");
    } finally {
      submit.disabled = false;
      submit.classList.remove("is-loading");
    }
  });
})();
