(() => {
  const { api, toast } = window.RIP;
  const app = document.getElementById("qr-app");
  if (!app) return;

  const image = document.getElementById("qr-image");
  const skeleton = document.getElementById("qr-skeleton");
  const content = document.getElementById("qr-content");
  const fullscreenButton = document.getElementById("fullscreen-btn");

  async function load() {
    try {
      const data = await api("/api/me/qr");
      if (data.image) {
        image.src = data.image;
        image.alt = `${data.name || "队员"}的身份二维码`;
        image.hidden = false;
        skeleton.hidden = true;
      }
      if (data.content) content.textContent = data.content;
    } catch (err) {
      skeleton.hidden = true;
      toast(err.message, "error");
    }
  }

  function setImmersive(on) {
    app.classList.toggle("is-immersive", on);
    fullscreenButton?.setAttribute("aria-pressed", String(on));
  }

  fullscreenButton?.addEventListener("click", async () => {
    const enter = !document.fullscreenElement && !app.classList.contains("is-immersive");
    try {
      if (enter && app.requestFullscreen) {
        await app.requestFullscreen();
      } else if (!enter && document.fullscreenElement) {
        await document.exitFullscreen();
      }
      setImmersive(enter);
    } catch {
      // iOS Safari does not expose element fullscreen; use the CSS-only mode.
      setImmersive(enter);
      if (enter) toast("已进入放大显示，请把屏幕亮度调到最高", "info");
    }
  });

  document.addEventListener("fullscreenchange", () => {
    setImmersive(Boolean(document.fullscreenElement));
  });

  load();
})();
