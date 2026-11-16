/* Shared API, formatting and UI helpers. */
(() => {
  const toastStack = document.getElementById("toast-stack");

  async function api(path, options = {}) {
    const opts = {
      method: options.method || "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(options.headers || {}) },
    };
    if (options.body !== undefined) {
      if (typeof options.body === "string") {
        opts.body = options.body;
      } else {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(options.body);
      }
    }
    let response;
    try {
      response = await fetch(path, opts);
    } catch {
      const error = new Error("网络连接失败，请检查移动数据后重试");
      error.code = "network_error";
      throw error;
    }

    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      const error = new Error(
        data?.error?.message || `请求失败（${response.status}）`,
      );
      error.status = response.status;
      error.code = data?.error?.code || "http_error";
      error.data = data;
      error.retryAfter = response.headers.get("Retry-After");
      throw error;
    }
    return data;
  }

  function toast(message, type = "info", duration = 3200) {
    if (!toastStack) return;
    const node = document.createElement("div");
    node.className = `toast${type === "error" ? " is-error" : type === "success" ? " is-success" : ""}`;
    const text = document.createElement("span");
    text.textContent = message;
    node.appendChild(text);
    toastStack.appendChild(node);
    window.setTimeout(() => {
      node.classList.add("is-leaving");
      window.setTimeout(() => node.remove(), 220);
    }, duration);
  }

  function setBusy(button, busy, busyText) {
    if (!button) return;
    if (busy) {
      if (!button.dataset.originalHtml) button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (busyText) button.textContent = busyText;
    } else {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      if (button.dataset.originalHtml) {
        button.innerHTML = button.dataset.originalHtml;
        delete button.dataset.originalHtml;
      }
    }
  }

  function showError(element, message) {
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
  }

  function hideError(element) {
    if (!element) return;
    element.hidden = true;
    element.textContent = "";
  }

  const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatDateTime(value) {
    const date = parseDate(value);
    return date ? dateTimeFormatter.format(date) : "—";
  }

  function formatTime(value) {
    const date = parseDate(value);
    return date ? timeFormatter.format(date) : "—";
  }

  function relativeTime(value) {
    const date = parseDate(value);
    if (!date) return "";
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 45) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return formatDateTime(value);
  }

  function initials(name) {
    const text = (name || "?").trim();
    return text ? Array.from(text)[0] : "?";
  }

  function debounce(fn, delay = 250) {
    let timer = null;
    return (...args) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fn(...args), delay);
    };
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function statusLabel(status) {
    return {
      active: "存活",
      eliminated: "已淘汰",
      unregistered: "未注册",
      revoked: "已撤销",
    }[status] || status || "—";
  }

  function statusClass(status) {
    return {
      active: "state-active",
      eliminated: "state-eliminated",
      unregistered: "state-unregistered",
      revoked: "state-revoked",
    }[status] || "";
  }

  window.RIP = {
    api,
    toast,
    setBusy,
    showError,
    hideError,
    formatDateTime,
    formatTime,
    relativeTime,
    initials,
    debounce,
    el,
    statusLabel,
    statusClass,
  };
})();
