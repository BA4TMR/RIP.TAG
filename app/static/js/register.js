(() => {
  const { api, setBusy, showError, hideError } = window.RIP;
  const form = document.getElementById("register-form");
  if (!form) return;

  const input = document.getElementById("name");
  const error = document.getElementById("form-error");
  const submit = document.getElementById("register-submit");
  const token = form.querySelector('input[name="ntag_token"]').value;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError(error);
    const name = input.value.trim().replace(/\s+/g, " ");
    if (!name) {
      showError(error, "请填写姓名后再继续");
      input.focus();
      return;
    }
    if (Array.from(name).length > 30) {
      showError(error, "姓名不能超过 30 个字符");
      input.focus();
      return;
    }

    setBusy(submit, true, "正在登记…");
    try {
      const data = await api("/api/register", {
        method: "POST",
        body: { ntag_token: token, name },
      });
      window.location.replace(data.redirect || "/home");
    } catch (err) {
      if (err.code === "already_registered") {
        window.location.replace(`/r/${encodeURIComponent(token)}`);
        return;
      }
      showError(error, err.message);
      setBusy(submit, false);
    }
  });

  input.addEventListener("input", () => {
    if (!error.hidden) hideError(error);
  });
})();
