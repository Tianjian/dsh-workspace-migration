#!/usr/bin/env node
// Materialise one session per workspace and attach it to the workspace record.
//
// Artifact: <sessionsRoot>/<projectKey(cwd)>/<encodeSegment(id)>/session.v4.jsonl.zstd
// Container: concatenated, independently decodable, checksummed zstd frames.
//   frame 1  exactly one header line
//   frame 2  the event lines
//
// v4 admission rules that are easy to miss (each one fails the whole log):
//   * message events need a producer-owned `source`  (assistant -> kind "model")
//   * message events need `surfaceOp` ("append" or a replace object)
//   * assistant/message needs `data.stream` (array) for settlement
//
// Requires Node >= 22 (node:zlib zstd support).
//
// Usage:
//   node write_sessions.js --manifest session-manifest.json \
//     --registry ~/.dsh/storages/workspace.json --sessions-root ~/.dsh/sessions
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const os = require('node:os');
const { randomUUID } = require('node:crypto');

const CHECKSUM = { params: { [zlib.constants.ZSTD_c_checksumFlag]: 1 } };

function encodeSegment(raw) {
  if (raw === '.') return '~002E';
  if (raw === '..') return '~002E~002E';
  let out = '';
  for (let i = 0; i < raw.length; i += 1) {
    const code = raw.charCodeAt(i);
    const ch = String.fromCharCode(code);
    if (ch !== '~' && /^[A-Za-z0-9._-]$/.test(ch)) out += ch;
    else out += `~${code.toString(16).toUpperCase().padStart(4, '0')}`;
  }
  return out;
}

function projectKey(cwd) {
  let readable = '';
  let separatorRun = false;
  for (let i = 0; i < cwd.length; i += 1) {
    const code = cwd.charCodeAt(i);
    const ch = String.fromCharCode(code);
    if (ch === '/' || ch === '\\' || ch === ':') {
      if (!separatorRun) readable += '-';
      separatorRun = true;
    } else if (ch !== '~' && /^[A-Za-z0-9._-]$/.test(ch)) {
      readable += ch;
      separatorRun = false;
    } else {
      readable += `~${code.toString(16).toUpperCase().padStart(4, '0')}`;
      separatorRun = false;
    }
  }
  return `--${(readable.replace(/^-+/, '') || 'root').slice(0, 251)}--`;
}

function events(entry, start) {
  let seq = 0;
  const next = () => seq++;
  return [
    { type: 'permission/preset', seq: next(), time: start + 1, data: { preset: 'workspace-write' } },
    { type: 'approval/policy', seq: next(), time: start + 2, data: { policy: 'never' } },
    { type: 'turn/start', seq: next(), time: start + 3, data: { turn: 1 } },
    { type: 'step/start', seq: next(), time: start + 4, data: { turn: 1, step: 1 } },
    {
      type: 'user/message', seq: next(), time: start + 5, surfaceOp: 'append',
      data: {
        content: [{ type: 'text', text: entry.question }],
        source: { kind: 'user', rpcId: randomUUID(), clientTimeZone: 'UTC' },
        role: 'user', id: randomUUID(),
      },
    },
    {
      type: 'session/title', seq: next(), time: start + 6,
      data: { title: entry.title, messageSeqs: [4], source: { kind: 'fallback' } },
    },
    {
      type: 'assistant/message', seq: next(), time: start + 7, surfaceOp: 'append',
      data: {
        turn: 1, step: 1, stream: [],
        message: {
          role: 'assistant',
          content: [{ type: 'text', text: entry.answer }],
          source: { kind: 'model', provider, model },
          id: randomUUID(),
        },
      },
    },
    { type: 'step/end', seq: next(), time: start + 8, data: { turn: 1, step: 1 } },
    { type: 'turn/end', seq: next(), time: start + 9, data: { turn: 1, reason: { kind: 'completed' } } },
  ];
}

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : process.argv[i + 1];
}

const manifestPath = arg('manifest');
const registryPath = arg('registry', path.join(os.homedir(), '.dsh', 'storages', 'workspace.json'));
const sessionsRoot = arg('sessions-root', path.join(os.homedir(), '.dsh', 'sessions'));
// Provenance stamped on the synthetic assistant message. Only `kind` is
// validated at load time, but keeping real values keeps the record coherent.
const provider = arg('provider', 'deepseek-account');
const model = arg('model', 'deepseek-chat');

if (!manifestPath) {
  console.error('usage: node write_sessions.js --manifest <session-manifest.json> '
    + '[--registry <workspace.json>] [--sessions-root <dir>]');
  process.exit(2);
}

function readJson(file) {
  // Windows PowerShell's `Set-Content -Encoding UTF8` writes a BOM; JSON.parse
  // rejects it with a confusing "Unexpected token '\uFEFF'".
  let text = fs.readFileSync(file, 'utf8');
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  return JSON.parse(text);
}

const manifest = readJson(manifestPath);
const registry = readJson(registryPath);
const table = registry.tables.workspaces;
const byTitle = new Map(Object.entries(table).map(([id, w]) => [w.title, id]));

const base = Date.now();
let made = 0;
for (const entry of manifest) {
  const workspaceId = entry.workspaceId ?? byTitle.get(entry.name);
  if (workspaceId === undefined || table[workspaceId] === undefined) {
    console.warn(`skip ${entry.name}: not registered`);
    continue;
  }
  const id = entry.sessionId ?? `session-${randomUUID()}`;
  const start = base + made * 1000;
  const header = {
    type: 'session', version: 4, id, createdAt: start, cwd: entry.cwd,
    isSeeded: false, delegationDepth: 0, agentPreset: 'standard',
  };
  const rows = events(entry, start);
  const headerText = `${JSON.stringify(header)}\n`;
  const bodyText = `${rows.map((e) => JSON.stringify(e)).join('\n')}\n`;

  const dir = path.join(sessionsRoot, projectKey(entry.cwd), encodeSegment(id));
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'session.v4.jsonl.zstd'),
    Buffer.concat([
      zlib.zstdCompressSync(Buffer.from(headerText, 'utf8'), CHECKSUM),
      zlib.zstdCompressSync(Buffer.from(bodyText, 'utf8'), CHECKSUM),
    ]),
  );

  table[workspaceId].sessionIds.push(id);
  table[workspaceId].updatedAt = new Date().toISOString();
  made += 1;
  console.log(`created ${entry.name} -> ${id}`);
}

const tmp = `${registryPath}.tmp-${process.pid}`;
fs.writeFileSync(tmp, `${JSON.stringify(registry, null, 2)}\n`, 'utf8');
fs.renameSync(tmp, registryPath);
console.log(`${made} session(s) written; registry updated`);
