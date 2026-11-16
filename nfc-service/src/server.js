"use strict";

/**
 * Local-only NFC writer.
 *
 * Security boundaries:
 *   - listens on 127.0.0.1 only
 *   - no database access, no credentials
 *   - the only write operation is "write this NTAG URL and read it back"
 *
 * The browser page runs on HTTPS. Modern Chromium treats loopback as a secure
 * context; the service still answers CORS and Private-Network preflights.
 */

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const { encodeNdefUrl, decodeNdefUrl } = require("./ndef");

const VERSION = "1.0.0";
const TOKEN_RE = /^[A-Za-z0-9_-]{16,128}$/;
const SERVICE_DIR = path.resolve(__dirname, "..");

function loadDotenv(file = path.join(SERVICE_DIR, ".env")) {
  if (!fs.existsSync(file)) return;
  const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if (!key || process.env[key] !== undefined) continue;
    if (
      value.length >= 2 &&
      value[0] === value[value.length - 1] &&
      (value[0] === "'" || value[0] === '"')
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

function envBool(name, fallback) {
  const value = process.env[name];
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function envInt(name, fallback) {
  const value = process.env[name];
  if (!value || !value.trim()) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) throw new Error(`${name} must be an integer`);
  return parsed;
}

function loadConfig() {
  loadDotenv();
  const publicBaseUrl = (process.env.PUBLIC_BASE_URL || "").trim().replace(/\/+$/, "");
  if (!publicBaseUrl) {
    throw new Error("PUBLIC_BASE_URL is required (e.g. https://activity.example.com)");
  }
  const parsed = new URL(publicBaseUrl);
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("PUBLIC_BASE_URL must be an http(s) URL");
  }
  const allowHttp = envBool("ALLOW_HTTP_URL", false);
  if (parsed.protocol !== "https:" && !allowHttp) {
    throw new Error("PUBLIC_BASE_URL must use HTTPS (set ALLOW_HTTP_URL=true only for local dev)");
  }

  const npcBaseUrl = (process.env.NPC_BASE_URL || "").trim().replace(/\/+$/, "");
  if (npcBaseUrl) {
    const parsedNpc = new URL(npcBaseUrl);
    if (!["http:", "https:"].includes(parsedNpc.protocol)) {
      throw new Error("NPC_BASE_URL must be an http(s) URL");
    }
    if (parsedNpc.protocol !== "https:" && !allowHttp) {
      throw new Error("NPC_BASE_URL must use HTTPS (set ALLOW_HTTP_URL=true only for local dev)");
    }
  }

  const configuredOrigins = (process.env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const origins = new Set([
    parsed.origin,
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    ...configuredOrigins,
  ]);

  return {
    port: envInt("PORT", 8765),
    publicBaseUrl,
    ntagOrigin: parsed.origin,
    npcBaseUrl: npcBaseUrl || null,
    npcOrigin: npcBaseUrl ? new URL(npcBaseUrl).origin : null,
    allowedOrigins: origins,
    readerName: (process.env.READER_NAME || process.env.NFC_READER || "").trim(),
    cardTimeoutMs: envInt("CARD_TIMEOUT_MS", 15000),
    allowHttp,
  };
}

class TagError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "TagError";
    this.status = status;
    this.code = code;
  }
}

function validateToken(token) {
  if (typeof token !== "string" || !TOKEN_RE.test(token)) {
    throw new TagError(
      400,
      "invalid_token",
      "ntag_token must be 16-128 URL-safe characters (A-Z, a-z, 0-9, _ or -)",
    );
  }
  return token;
}

function buildNtagUrl(baseUrl, token) {
  validateToken(token);
  const parsed = new URL(baseUrl);
  return `${parsed.origin}/r/${token}`;
}

function buildNpcUrl(baseUrl, token) {
  validateToken(token);
  if (!baseUrl) {
    throw new TagError(503, "npc_base_url_missing", "未配置 NPC_BASE_URL，无法写 NPC 标签");
  }
  const parsed = new URL(baseUrl);
  return `${parsed.origin}/staff/${token}`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mapReaderError(error) {
  if (error instanceof TagError) return error;
  const message = error && error.message ? error.message : String(error || "unknown error");
  if (/no card|card.*(removed|not present)|was removed/i.test(message)) {
    return new TagError(409, "card_removed", "卡片已离开读卡器，请放稳后重试");
  }
  if (/timeout|timed out/i.test(message)) {
    return new TagError(504, "card_timeout", "等待卡片超时，请把 NTAG 放稳后重试");
  }
  return new TagError(502, "write_failed", `写卡失败：${message}`);
}

class ReaderBridge {
  constructor(config) {
    this.config = config;
    this.nfc = null;
    this.readers = new Map();
    this.cards = new Map();
    this.lastError = null;
    this.busy = false;
  }

  start() {
    try {
      // Lazy require so the HTTP health endpoint still starts on a development
      // machine without PC/SC native modules installed.
      const { NFC } = require("nfc-pcsc");
      this.nfc = new NFC();
    } catch (error) {
      this.lastError = `nfc-pcsc unavailable: ${error.message}`;
      return false;
    }

    this.nfc.on("reader", (reader) => {
      const name = reader.reader ? reader.reader.name : reader.name || "unknown";
      this.readers.set(name, reader);
      this.lastError = null;
      console.log(`[nfc] reader attached: ${name}`);

      reader.on("card", (card) => {
        this.cards.set(name, card);
        console.log(`[nfc] card present on ${name}`);
      });
      reader.on("card.off", () => {
        this.cards.delete(name);
        console.log(`[nfc] card removed from ${name}`);
      });
      reader.on("error", (error) => {
        this.lastError = error.message || String(error);
        console.error(`[nfc] reader error on ${name}: ${this.lastError}`);
      });
      reader.on("end", () => {
        this.readers.delete(name);
        this.cards.delete(name);
        console.log(`[nfc] reader detached: ${name}`);
      });
    });

    this.nfc.on("error", (error) => {
      this.lastError = error.message || String(error);
      console.error(`[nfc] service error: ${this.lastError}`);
    });
    return true;
  }

  status() {
    const readers = [...this.readers.keys()];
    const selected = this.selectReader(false);
    return {
      reader_connected: readers.length > 0,
      card_present: Boolean(selected && this.cards.has(selected.name)),
      reader: selected ? selected.name : readers[0] || null,
      readers,
      error: this.lastError,
    };
  }

  selectReader(throwOnMultiple = true) {
    const readers = [...this.readers.values()];
    if (!readers.length) return null;
    if (this.config.readerName) {
      const exact = readers.find(
        (reader) => (reader.reader ? reader.reader.name : reader.name) === this.config.readerName,
      );
      if (exact) return exact;
      const partial = readers.find((reader) =>
        (reader.reader ? reader.reader.name : reader.name).includes(this.config.readerName),
      );
      if (partial) return partial;
      if (throwOnMultiple) {
        throw new TagError(
          503,
          "reader_not_found",
          `未找到指定读卡器「${this.config.readerName}」`,
        );
      }
      return readers[0];
    }
    if (readers.length > 1 && throwOnMultiple) {
      throw new TagError(
        409,
        "multiple_readers",
        "检测到多个读卡器，请只保留一个，或设置 READER_NAME 指定读卡器",
      );
    }
    return readers[0];
  }

  async waitForReader(timeoutMs) {
    if (!this.nfc) {
      throw new TagError(
        503,
        "reader_unavailable",
        this.lastError || "nfc-pcsc 未安装或加载失败",
      );
    }
    const started = Date.now();
    while (!this.readers.size) {
      if (Date.now() - started > timeoutMs) {
        throw new TagError(
          503,
          "reader_unavailable",
          "未检测到 PC/SC 读卡器，请检查 USB 连接和驱动",
        );
      }
      await sleep(120);
    }
  }

  waitForCard(reader, timeoutMs) {
    const name = reader.reader ? reader.reader.name : reader.name || "unknown";
    if (this.cards.has(name)) return Promise.resolve(this.cards.get(name));

    return new Promise((resolve, reject) => {
      let settled = false;
      const cleanup = () => {
        settled = true;
        clearTimeout(timer);
        reader.removeListener("card", onCard);
        reader.removeListener("end", onEnd);
      };
      const onCard = (card) => {
        if (settled) return;
        cleanup();
        resolve(card);
      };
      const onEnd = () => {
        if (settled) return;
        cleanup();
        reject(new TagError(503, "reader_removed", "读卡器已断开"));
      };
      const timer = setTimeout(() => {
        if (settled) return;
        cleanup();
        reject(new TagError(504, "card_not_found", "未检测到卡片，请把 NTAG 放稳后重试"));
      }, timeoutMs);
      reader.once("card", onCard);
      reader.once("end", onEnd);
    });
  }

  async write(targetUrl) {
    if (this.busy) {
      throw new TagError(409, "write_in_progress", "上一次写卡尚未结束，请稍候");
    }
    let parsedTarget;
    try {
      parsedTarget = new URL(targetUrl);
    } catch {
      throw new TagError(400, "invalid_url", "写入 URL 无效");
    }
    if (!["http:", "https:"].includes(parsedTarget.protocol)) {
      throw new TagError(400, "invalid_url", "写入 URL 必须是 http(s)");
    }
    this.busy = true;
    try {
      await this.waitForReader(Math.min(this.config.cardTimeoutMs, 5000));
      const reader = this.selectReader(true);
      if (!reader) {
        throw new TagError(503, "reader_unavailable", "未检测到 PC/SC 读卡器");
      }
      await this.waitForCard(reader, this.config.cardTimeoutMs);

      let cc;
      try {
        cc = await reader.read(3, 4);
      } catch (error) {
        throw new TagError(502, "not_ntag", "无法读取标签能力页，请确认是 NTAG215/216");
      }
      if (!cc || cc.length < 4 || cc[0] !== 0xe1 || cc[1] !== 0x10) {
        throw new TagError(400, "not_ntag", "标签不是 NDEF 格式的 NTAG215/216");
      }
      const capacity = cc[2] * 8;
      if (capacity < 48 || capacity > 8192) {
        throw new TagError(400, "not_ntag", "标签容量异常，无法安全写入");
      }

      const encoded = encodeNdefUrl(targetUrl);
      if (encoded.padded.length > capacity) {
        throw new TagError(400, "tag_too_small", "标签容量不足，无法写入该 URL");
      }

      try {
        await reader.write(4, encoded.padded);
      } catch (error) {
        throw mapReaderError(error);
      }

      await sleep(80);
      let raw;
      try {
        raw = await reader.read(4, capacity);
      } catch (error) {
        throw mapReaderError(error);
      }
      let readbackUrl;
      try {
        readbackUrl = decodeNdefUrl(raw);
      } catch (error) {
        throw new TagError(502, "readback_parse_failed", `回读解析失败：${error.message}`);
      }
      if (readbackUrl !== targetUrl) {
        throw new TagError(
          502,
          "readback_mismatch",
          `回读不一致：期望 ${targetUrl}，实际 ${readbackUrl || "空标签"}`,
        );
      }
      return { readback_url: readbackUrl };
    } finally {
      this.busy = false;
    }
  }
}

function sendJson(response, status, payload, extraHeaders = {}) {
  const body = Buffer.from(JSON.stringify(payload), "utf8");
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    ...extraHeaders,
  });
  response.end(body);
}

function readBody(request, limit = 8192) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new TagError(413, "body_too_large", "请求体过大"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function createServer(config) {
  const bridge = new ReaderBridge(config);
  bridge.start();

  const server = http.createServer(async (request, response) => {
    const origin = request.headers.origin;
    const corsAllowed = !origin || config.allowedOrigins.has(origin);
    const corsHeaders = {};
    if (origin && corsAllowed) {
      corsHeaders["Access-Control-Allow-Origin"] = origin;
      corsHeaders["Vary"] = "Origin";
      corsHeaders["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
      corsHeaders["Access-Control-Allow-Headers"] = "Content-Type";
      corsHeaders["Access-Control-Allow-Private-Network"] = "true";
    }

    if (request.method === "OPTIONS") {
      if (!corsAllowed) {
        sendJson(response, 403, { ok: false, code: "origin_denied", error: "Origin 不在白名单" });
        return;
      }
      response.writeHead(204, corsHeaders);
      response.end();
      return;
    }
    if (!corsAllowed) {
      sendJson(response, 403, { ok: false, code: "origin_denied", error: "Origin 不在白名单" });
      return;
    }

    const url = new URL(request.url || "/", `http://127.0.0.1:${config.port}`);
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        sendJson(
          response,
          200,
          {
            ok: true,
            service: "riptag-nfc-writer",
            version: VERSION,
            ...bridge.status(),
          },
          corsHeaders,
        );
        return;
      }

      if (request.method === "POST" && url.pathname === "/write") {
        let payload;
        try {
          payload = JSON.parse(await readBody(request));
        } catch (error) {
          if (error instanceof TagError) throw error;
          throw new TagError(400, "invalid_json", "请求体必须是 JSON");
        }
        let targetUrl;
        if (payload && Object.prototype.hasOwnProperty.call(payload, "npc_token")) {
          const npcToken = validateToken(payload.npc_token);
          targetUrl = buildNpcUrl(config.npcBaseUrl, npcToken);
        } else {
          const token = validateToken(payload && payload.ntag_token);
          targetUrl = buildNtagUrl(config.publicBaseUrl, token);
        }
        const result = await bridge.write(targetUrl);
        sendJson(
          response,
          200,
          { ok: true, readback_url: result.readback_url },
          corsHeaders,
        );
        return;
      }

      sendJson(response, 404, { ok: false, code: "not_found", error: "接口不存在" }, corsHeaders);
    } catch (error) {
      const normalized =
        error instanceof TagError
          ? error
          : new TagError(500, "internal_error", error.message || "写入服务内部错误");
      if (!(error instanceof TagError)) console.error("[nfc] request failed:", error);
      sendJson(
        response,
        normalized.status,
        { ok: false, code: normalized.code, error: normalized.message },
        corsHeaders,
      );
    }
  });

  server.on("error", (error) => {
    console.error(`[nfc] HTTP server error: ${error.message}`);
  });

  return { server, bridge };
}

function startServer(config) {
  const { server, bridge } = createServer(config);
  server.listen(config.port, "127.0.0.1", () => {
    console.log(`[nfc] listening on http://127.0.0.1:${config.port}`);
    console.log(`[nfc] NTAG URL base: ${config.ntagOrigin}/r/`);
    console.log(`[nfc] allowed origins: ${[...config.allowedOrigins].join(", ")}`);
    const status = bridge.status();
    if (!status.reader_connected) {
      console.warn("[nfc] no reader detected yet; plug in a PC/SC reader");
    }
  });

  const shutdown = () => {
    console.log("[nfc] shutting down");
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 2000).unref();
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  return { server, bridge };
}

if (require.main === module) {
  try {
    const config = loadConfig();
    startServer(config);
  } catch (error) {
    console.error(`[nfc] startup failed: ${error.message}`);
    process.exit(1);
  }
}

module.exports = {
  VERSION,
  TagError,
  ReaderBridge,
  createServer,
  startServer,
  loadConfig,
  validateToken,
  buildNtagUrl,
  buildNpcUrl,
};
