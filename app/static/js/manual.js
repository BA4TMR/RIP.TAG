(() => {
  const { api, setBusy, showError, hideError, toast } = window.RIP;
  const form = document.getElementById("manual-form");
  if (!form) return;

  const input = document.getElementById("kill-code");
  const slots = document.getElementById("code-slots");
  const error = document.getElementById("form-error");
  const submit = document.getElementById("manual-submit");
  const slotNodes = Array.from(slots.querySelectorAll("span"));

  function normalize(value) {
    return String(value || "")
      .normalize("NFKC")
      .replace(/[^A-Za-z0-9]/g, "")
      .toUpperCase()
      .slice(0, 6);
  }

  function render() {
    const value = normalize(input.value);
    if (input.value !== value) input.value = value;
    slotNodes.forEach((slot, index) => {
      slot.textContent = value[index] || "";
      slot.classList.toggle("is-filled", Boolean(value[index]));
      slot.classList.toggle("is-cursor", index === value.length && document.activeElement === input);
    });
    slots.classList.toggle("is-focused", document.activeElement === input);
  }

  slots.addEventListener("click", () => input.focus());
  input.addEventListener("input", () => {
    hideError(error);
    render();
  });
  input.addEventListener("focus", () => {
    slots.classList.add("is-focused");
    render();
  });
  input.addEventListener("blur", () => {
    slots.classList.remove("is-focused");
    render();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError(error);
    const code = normalize(input.value);
    input.value = code;
    render();
    if (code.length !== 6) {
      showError(error, "请输入完整的 6 位击杀码");
      input.focus();
      return;
    }

    setBusy(submit, true, "正在查询…");
    try {
      const data = await api("/api/kills/lookup", {
        method: "POST",
        body: { code },
      });
      if (!data.can_kill) {
        showError(error, data.message || "该队员当前无法被击杀");
        toast(data.message || "该队员当前无法被击杀", "error");
        setBusy(submit, false);
        input.select();
        return;
      }
      window.location.assign(`/kill/confirm?code=${encodeURIComponent(code)}`);
    } catch (err) {
      showError(error, err.message);
      setBusy(submit, false);
      if (err.status === 401) {
        window.location.replace("/");
        return;
      }
      if (err.status === 429) {
        toast("尝试次数过多，请稍后再试", "error", 4500);
      }
      input.select();
    }
  });

  render();
})();
