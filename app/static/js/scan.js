(() => {
  "use strict";

  const { api, toast, setBusy } = window.RIP;
  const app = document.getElementById("scan-app");
  if (!app) return;

  const startButton = document.getElementById("start-camera");
  const placeholder = document.getElementById("camera-placeholder");
  const status = document.getElementById("camera-status");
  const statusText = document.getElementById("status-text");
  const spinner = document.getElementById("status-spinner");
  const fileInput = document.getElementById("scan-file");
  const blocked = app.dataset.status === "eliminated";

  let scanner = null;
  let running = false;
  let processing = false;
  let restartTimer = null;

  function setStatus(message, kind = "idle", loading = false) {
    if (!status || !statusText) return;
    statusText.textContent = message;
    status.classList.toggle("is-error", kind === "error");
    if (spinner) spinner.hidden = !loading;
  }

  function cleanCode(raw) {
    return String(raw || "")
      .normalize("NFKC")
      .replace(/[^A-Za-z0-9]/g, "")
      .toUpperCase()
      .slice(0, 6);
  }

  function errorName(error) {
    return String(error?.name || "").trim();
  }

  function friendlyCameraError(error) {
    const name = errorName(error);
    if (name === "NotAllowedError" || name === "SecurityError") {
      return "摄像头权限被拒绝：请点地址栏锁图标 → 网站设置 → 允许摄像头，然后刷新";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "没有检测到摄像头，请使用“拍照识别”或手动输入";
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return "摄像头被其他应用占用，请关闭相机/会议软件后重试";
    }
    if (name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") {
      return "摄像头不支持当前取景参数，请使用拍照识别或手动输入";
    }
    if (!window.isSecureContext) {
      return "摄像头只能在 HTTPS 页面使用";
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      return "当前浏览器不支持网页摄像头，请使用拍照识别或手动输入";
    }
    return `摄像头启动失败（${name || "未知错误"}），请使用拍照识别或手动输入`;
  }

  async function stopCamera() {
    if (!scanner || !running) return;
    try {
      await scanner.stop();
    } catch {
      // The camera may already have been released by the OS.
    }
    running = false;
    app.classList.remove("is-scanning");
  }

  async function handleScan(decodedText) {
    if (processing || blocked) return;
    const code = cleanCode(decodedText);
    if (code.length !== 6) {
      setStatus("未识别到有效击杀码，请重新对准", "error");
      return;
    }
    processing = true;
    window.clearTimeout(restartTimer);
    await stopCamera();
    setStatus("识别成功，正在查询目标…", "idle", true);

    try {
      const data = await api("/api/kills/lookup", {
        method: "POST",
        body: { code },
      });
      if (!data.can_kill) {
        setStatus(data.message || "该队员当前无法被击杀", "error");
        toast(data.message || "该队员当前无法被击杀", "error");
        processing = false;
        return;
      }
      window.location.assign(`/kill/confirm?code=${encodeURIComponent(code)}`);
    } catch (err) {
      setStatus(err.message, "error");
      toast(err.message, "error");
      processing = false;
      if (err.status === 401) {
        window.location.replace("/");
        return;
      }
      if (err.status === 429) {
        placeholder.hidden = false;
        return;
      }
      restartTimer = window.setTimeout(startCamera, 1600);
    }
  }

  function cameraCallbacks() {
    return {
      qrbox: (viewfinderWidth, viewfinderHeight) => {
        const edge = Math.floor(Math.min(viewfinderWidth, viewfinderHeight) * 0.7);
        return { width: edge, height: edge };
      },
      aspectRatio: 1.0,
      disableFlip: false,
      fps: 10,
    };
  }

  async function startCamera() {
    if (blocked || running || processing) return;
    if (!window.Html5Qrcode) {
      setStatus("扫码组件加载失败，请刷新页面或使用拍照识别/手动输入", "error");
      toast("扫码组件加载失败，请刷新页面或使用拍照识别", "error");
      return;
    }
    if (!window.isSecureContext) {
      setStatus("摄像头需要 HTTPS 环境，请使用 https:// 地址", "error");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("当前浏览器不支持网页摄像头，请使用拍照识别或手动输入", "error");
      return;
    }

    window.clearTimeout(restartTimer);
    setStatus("正在启动摄像头…", "idle", true);
    setBusy(startButton, true, "正在启动…");

    try {
      scanner = scanner || new window.Html5Qrcode("reader", { verbose: false });

      // `ideal` is important: desktop Chrome may only have a front/user camera,
      // and Huawei browsers may not expose a distinct environment camera.
      const attempts = [
        { facingMode: { ideal: "environment" } },
        { facingMode: { ideal: "user" } },
        { facingMode: "environment" },
        { facingMode: "user" },
      ];
      // Some Android/Huawei browsers accept getUserMedia but reject
      // facingMode in the QR library. Try each enumerated camera explicitly.
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        for (const device of devices) {
          if (device.kind === "videoinput" && device.deviceId) {
            attempts.push({ deviceId: { exact: device.deviceId } });
          }
        }
      } catch {
        // enumerateDevices is optional; the facingMode attempts still run.
      }
      let lastError = null;
      let started = false;
      for (const config of attempts) {
        try {
          await scanner.start(
            config,
            cameraCallbacks(),
            (decodedText) => handleScan(decodedText),
            () => {},
          );
          started = true;
          break;
        } catch (error) {
          lastError = error;
          const name = errorName(error);
          console.warn("[scan] camera attempt failed", config, error);
          // Do not repeatedly prompt after an explicit denial.
          if (name === "NotAllowedError" || name === "SecurityError") break;
          try {
            await scanner.clear();
          } catch {
            // Ignore clear failures; the next start may still work.
          }
          scanner = new window.Html5Qrcode("reader", { verbose: false });
        }
      }
      if (!started) throw lastError || new Error("camera_start_failed");

      running = true;
      app.classList.add("is-scanning");
      const video = document.querySelector("#reader video");
      if (video) {
        video.setAttribute("playsinline", "true");
        video.muted = true;
        video.play?.().catch(() => {});
      }
      setStatus("正在识别，请将二维码放入取景框", "idle");
      if (placeholder) placeholder.hidden = true;
    } catch (err) {
      running = false;
      setBusy(startButton, false);
      if (placeholder) placeholder.hidden = false;
      const message = friendlyCameraError(err);
      setStatus(message, "error");
      toast(message, "error", 6000);
    } finally {
      setBusy(startButton, false);
    }
  }

  async function scanFromFile(file) {
    if (!file || blocked || processing) return;
    if (!window.Html5Qrcode) {
      setStatus("扫码组件加载失败，请手动输入", "error");
      return;
    }
    window.clearTimeout(restartTimer);
    await stopCamera();
    processing = true;
    setStatus("正在识别照片中的二维码…", "idle", true);
    try {
      const fileScanner = new window.Html5Qrcode("reader", { verbose: false });
      const decoded = await fileScanner.scanFile(file, false);
      processing = false;
      await handleScan(decoded);
    } catch (error) {
      processing = false;
      setStatus("照片中没有识别到二维码，请对准后重拍或手动输入", "error");
      toast("照片中没有识别到二维码，请重拍或手动输入", "error");
    } finally {
      if (fileInput) fileInput.value = "";
    }
  }

  startButton?.addEventListener("click", startCamera);
  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) scanFromFile(file);
  });

  window.addEventListener("pagehide", () => {
    window.clearTimeout(restartTimer);
    stopCamera();
  });

  if (blocked) {
    setStatus("你已被淘汰，无法继续击杀", "error");
    if (startButton) startButton.disabled = true;
  } else {
    setStatus("点击下方按钮开启摄像头，或拍照识别");
  }
})();
