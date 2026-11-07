#!/usr/bin/env node
// Decode a concatenated multi-frame zstd JSONL session log into plain JSONL.
//
// Node's one-shot zstd API (`zstdDecompressSync`) and even the stream decoder
// stop at the first frame — for a DSH session that is just the 244-byte header
// line, which makes an intact log look corrupt. Frames are therefore located
// structurally (RFC 8878) and decoded one by one.
//
// Usage:
//   node decode_session.js <session.v4.jsonl.zstd> [out.jsonl]
'use strict';

const fs = require('node:fs');
const zlib = require('node:zlib');

const ZSTD_MAGIC = 0xfd2fb528;

function scanFrames(buf) {
  const frames = [];
  const dictBytes = [0, 1, 2, 4];
  let pos = 0;
  while (pos + 4 <= buf.length) {
    if (buf.readUInt32LE(pos) !== ZSTD_MAGIC) throw new Error(`invalid frame magic at ${pos}`);
    const start = pos;
    pos += 4;
    const descriptor = buf[pos++];
    const fcsFlag = descriptor >>> 6;
    const singleSegment = (descriptor >>> 5) & 1;
    const checksumFlag = (descriptor >>> 2) & 1;
    const dictIdFlag = descriptor & 3;
    if (!singleSegment) pos += 1; // window descriptor
    pos += dictBytes[dictIdFlag];
    const fcsBytes = fcsFlag === 0 ? (singleSegment ? 1 : 0)
      : fcsFlag === 1 ? 2 : fcsFlag === 2 ? 4 : 8;
    pos += fcsBytes;
    for (;;) {
      const header = buf[pos] | (buf[pos + 1] << 8) | (buf[pos + 2] << 16);
      pos += 3;
      const last = header & 1;
      const type = (header >>> 1) & 3;
      const size = header >>> 3;
      if (type === 1) pos += 1;            // RLE block
      else if (type === 0 || type === 2) pos += size; // raw / compressed
      else throw new Error(`reserved block type at ${pos}`);
      if (last) break;
    }
    if (checksumFlag) pos += 4;
    frames.push({ start, end: pos });
  }
  return frames;
}

const input = process.argv[2];
if (!input) {
  console.error('usage: node decode_session.js <session.v4.jsonl.zstd> [out.jsonl]');
  process.exit(2);
}

const buf = fs.readFileSync(input);
const frames = scanFrames(buf);
const text = Buffer.concat(frames.map((f) => zlib.zstdDecompressSync(buf.subarray(f.start, f.end))))
  .toString('utf8');

if (process.argv[3]) {
  fs.writeFileSync(process.argv[3], text);
}

const lines = text.split('\n').filter(Boolean);
const types = {};
for (const line of lines) {
  try {
    const obj = JSON.parse(line);
    types[obj.type ?? 'unknown'] = (types[obj.type ?? 'unknown'] ?? 0) + 1;
  } catch {
    types.__bad__ = (types.__bad__ ?? 0) + 1;
  }
}
console.log(JSON.stringify({ frames: frames.length, bytes: text.length, lines: lines.length, types }, null, 1));
