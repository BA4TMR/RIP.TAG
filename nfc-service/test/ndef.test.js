"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  encodeNdefUrl,
  decodeNdefUrl,
  encodeUriMessage,
  encodeNdefTlv,
  parseNdefTlv,
  parseUriMessage,
} = require("../src/ndef");
const { buildNtagUrl, buildNpcUrl, validateToken } = require("../src/server");

const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWx";

test("encodes an https URL with the standard URI prefix and reads it back", () => {
  const url = `https://activity.example.com/r/${TOKEN}`;
  const encoded = encodeNdefUrl(url);
  assert.equal(encoded.message[0], 0xd1);
  assert.equal(encoded.message[2], encoded.message.length - 4);
  assert.equal(encoded.message[3], 0x55);
  assert.equal(encoded.message[4], 0x04);
  assert.equal(decodeNdefUrl(encoded.padded), url);
});

test("TLV is padded to a four-byte page boundary", () => {
  const encoded = encodeNdefUrl("https://example.com/r/abcdefghijklmnop");
  assert.equal(encoded.padded.length % 4, 0);
  assert.equal(encoded.padded[encoded.padded.length - 1], 0x00);
});

test("long NDEF records and long TLVs round-trip", () => {
  const longUrl = `https://example.com/r/${"a".repeat(300)}`;
  const message = encodeUriMessage(longUrl);
  assert.equal(message[0], 0xc1);
  assert.ok(message.length > 255);
  const tlv = encodeNdefTlv(message);
  assert.equal(tlv[0], 0x03);
  assert.equal(tlv[1], 0xff);
  assert.equal(parseUriMessage(parseNdefTlv(tlv)), longUrl);
});

test("empty tag data decodes as null", () => {
  assert.equal(decodeNdefUrl(Buffer.from([0xfe, 0x00, 0x00, 0x00])), null);
});

test("rejects a non-URI first record", () => {
  const textRecord = Buffer.from([0xd1, 0x01, 0x03, 0x54, 0x02, 0x65, 0x6e]);
  const tlv = encodeNdefTlv(textRecord);
  assert.throws(() => decodeNdefUrl(tlv), /not a URI/);
});

test("rejects truncated data instead of reporting a false match", () => {
  const encoded = encodeNdefUrl("https://example.com/r/abcdefghijklmnop");
  assert.throws(() => decodeNdefUrl(encoded.padded.subarray(0, 12)), /truncated/);
});

test("builds the exact NTAG URL from the configured public origin", () => {
  assert.equal(
    buildNtagUrl("https://activity.example.com/some/path", TOKEN),
    `https://activity.example.com/r/${TOKEN}`,
  );
});

test("builds the NPC /staff URL from the configured NPC origin", () => {
  assert.equal(
    buildNpcUrl("https://npc.example.com/some/path", TOKEN),
    `https://npc.example.com/staff/${TOKEN}`,
  );
  assert.throws(() => buildNpcUrl("", TOKEN), /NPC_BASE_URL/);
});

test("validates tokens before any hardware operation", () => {
  assert.equal(validateToken(TOKEN), TOKEN);
  for (const bad of ["short", "has space in token", "abc/def", ""]) {
    assert.throws(() => validateToken(bad), /ntag_token/);
  }
});
