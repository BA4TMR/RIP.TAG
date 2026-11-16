"use strict";

/**
 * Minimal NDEF URI encoder/parser for NTAG21x tags.
 *
 * The footprint is deliberately small: one well-known URI record, a Type 2
 * Tag TLV wrapper, and a parser used to verify what was actually read back.
 * Nothing here talks to hardware, so it is fully unit-testable.
 */

const URI_PREFIXES = [
  [0x00, ""],
  [0x01, "http://www."],
  [0x02, "https://www."],
  [0x03, "http://"],
  [0x04, "https://"],
  [0x05, "tel:"],
  [0x06, "mailto:"],
  [0x07, "ftp://anonymous:anonymous@"],
  [0x08, "ftp://ftp."],
  [0x09, "ftps://"],
  [0x0a, "sftp://"],
  [0x0b, "smb://"],
  [0x0c, "nfs://"],
  [0x0d, "ftp://"],
  [0x0e, "dav://"],
  [0x0f, "news:"],
  [0x10, "telnet://"],
  [0x11, "imap:"],
  [0x12, "rtsp://"],
  [0x13, "urn:"],
  [0x14, "pop:"],
  [0x15, "sip:"],
  [0x16, "sips:"],
  [0x17, "tftp:"],
  [0x18, "btspp://"],
  [0x19, "btl2cap://"],
  [0x1a, "btgoep://"],
  [0x1b, "tcpobex://"],
  [0x1c, "irdaobex://"],
  [0x1d, "file://"],
  [0x1e, "urn:epc:id:"],
  [0x1f, "urn:epc:tag:"],
  [0x20, "urn:epc:pat:"],
  [0x21, "urn:epc:raw:"],
  [0x22, "urn:epc:"],
  [0x23, "urn:nfc:"],
];

const PREFIX_BY_CODE = new Map(URI_PREFIXES.map(([code, prefix]) => [code, prefix]));

function encodeUriPayload(url) {
  if (typeof url !== "string" || !url) {
    throw new TypeError("URI must be a non-empty string");
  }
  let best = URI_PREFIXES[0];
  for (const candidate of URI_PREFIXES) {
    if (candidate[1] && url.startsWith(candidate[1]) && candidate[1].length > best[1].length) {
      best = candidate;
    }
  }
  const rest = url.slice(best[1].length);
  return Buffer.concat([Buffer.from([best[0]]), Buffer.from(rest, "utf8")]);
}

function encodeUriMessage(url) {
  const payload = encodeUriPayload(url);
  if (payload.length > 0xffffffff) throw new RangeError("URI is too long for NDEF");

  let header;
  let lengthBytes;
  if (payload.length <= 0xff) {
    header = 0xd1; // MB=1, ME=1, SR=1, TNF=well-known
    lengthBytes = Buffer.from([payload.length]);
  } else {
    header = 0xc1; // MB=1, ME=1, SR=0, TNF=well-known
    lengthBytes = Buffer.alloc(4);
    lengthBytes.writeUInt32BE(payload.length, 0);
  }
  return Buffer.concat([
    Buffer.from([header, 0x01]), // type length = 1
    lengthBytes,
    Buffer.from("U", "ascii"),
    payload,
  ]);
}

function encodeNdefTlv(message) {
  if (!Buffer.isBuffer(message) || !message.length) {
    throw new TypeError("NDEF message must be a non-empty Buffer");
  }
  let header;
  if (message.length < 0xff) {
    header = Buffer.from([0x03, message.length]);
  } else if (message.length <= 0xffff) {
    header = Buffer.alloc(4);
    header[0] = 0x03;
    header[1] = 0xff;
    header.writeUInt16BE(message.length, 2);
  } else {
    throw new RangeError("NDEF message is too large for a Type 2 Tag TLV");
  }
  return Buffer.concat([header, message, Buffer.from([0xfe])]);
}

function parseNdefTlv(data) {
  if (!Buffer.isBuffer(data) || data.length < 4) {
    throw new Error("tag data is too short");
  }
  let offset = 0;
  while (offset < data.length && data[offset] === 0x00) offset += 1;
  if (offset >= data.length) throw new Error("empty tag: no TLV found");
  if (data[offset] === 0xfe) return null;
  if (data[offset] !== 0x03) {
    throw new Error(`unexpected TLV type 0x${data[offset].toString(16)}`);
  }
  offset += 1;
  if (offset >= data.length) throw new Error("truncated TLV length");

  let length;
  if (data[offset] < 0xff) {
    length = data[offset];
    offset += 1;
  } else {
    if (offset + 3 > data.length) throw new Error("truncated long TLV length");
    length = data.readUInt16BE(offset + 1);
    offset += 3;
  }
  if (offset + length > data.length) {
    throw new Error("truncated NDEF message");
  }
  return data.subarray(offset, offset + length);
}

function parseUriMessage(message) {
  if (!Buffer.isBuffer(message) || message.length < 4) {
    throw new Error("NDEF message is too short");
  }
  let offset = 0;
  const results = [];

  while (offset < message.length) {
    const header = message[offset];
    offset += 1;
    const tnf = header & 0x07;
    const shortRecord = Boolean(header & 0x10);
    const hasIdLength = Boolean(header & 0x08);
    const typeLength = message[offset];
    offset += 1;

    let payloadLength;
    if (shortRecord) {
      payloadLength = message[offset];
      offset += 1;
    } else {
      if (offset + 4 > message.length) throw new Error("truncated NDEF payload length");
      payloadLength = message.readUInt32BE(offset);
      offset += 4;
    }
    let idLength = 0;
    if (hasIdLength) {
      idLength = message[offset];
      offset += 1;
    }
    if (offset + typeLength + idLength + payloadLength > message.length) {
      throw new Error("truncated NDEF record");
    }
    const type = message.subarray(offset, offset + typeLength);
    offset += typeLength + idLength;
    const payload = message.subarray(offset, offset + payloadLength);
    offset += payloadLength;

    if (tnf === 0x01 && type.length === 1 && type[0] === 0x55) {
      if (!payload.length) throw new Error("empty URI record");
      const prefix = PREFIX_BY_CODE.get(payload[0]);
      if (prefix === undefined) throw new Error("unknown URI prefix code");
      results.push(prefix + payload.subarray(1).toString("utf8"));
    } else {
      results.push(null);
    }

    if (header & 0x40) break; // ME
  }

  if (!results.length) throw new Error("no NDEF record found");
  if (results[0] === null) throw new Error("first NDEF record is not a URI");
  return results[0];
}

function decodeNdefUrl(data) {
  const message = parseNdefTlv(data);
  if (message === null) return null;
  return parseUriMessage(message);
}

function encodeNdefUrl(url) {
  const message = encodeUriMessage(url);
  const tlv = encodeNdefTlv(message);
  const padded = Buffer.alloc(Math.ceil(tlv.length / 4) * 4, 0x00);
  tlv.copy(padded);
  return { message, tlv, padded };
}

module.exports = {
  URI_PREFIXES,
  encodeUriPayload,
  encodeUriMessage,
  encodeNdefTlv,
  parseNdefTlv,
  parseUriMessage,
  encodeNdefUrl,
  decodeNdefUrl,
};
