# dsh-workspace-migration

把**另一个桌面 AI 编码助手**的工作区，完整迁移进 [DeepSeek Harness](https://github.com/deepseek-ai)（下称 DSH）——不只是复制文件，还要**注册成 DSH 的工作区**、让侧边栏能点进去，并为每个工作区生成一条「内容与产出物」会话。

这份仓库是那次迁移的复盘与工具化：一边是可复用的脚本，一边是把 DSH 内部存储格式、启动时机、会话格式三条硬校验全部踩过一遍后的笔记。

> 迁移体量参考（一次真实案例，已脱敏）：**11 个工作区 / 10,322 个文件 / 1.22 GB**，逐文件 SHA-256 全部一致，11/11 通过。

---

## TL;DR

1. **DSH 的工作区注册表是 JSON，但只在进程启动时读一次。** 改完文件**必须重启**应用，否则侧边栏永远看不到新工作区。
2. **重启必须"硬终止"，不能用优雅退出。** 优雅退出时宿主会把内存里的旧注册表刷回磁盘，直接覆盖掉刚迁移进去的记录（这个坑踩过一次，12 个工作区被打回 1 个的教训）。
3. **工作区只决定"能不能看到"，会话决定"点开有没有内容"。** 会话是独立的 `zstd` 多帧 JSONL 日志，v4 格式有三条硬校验：`message.source`、`surfaceOp`、`data.stream`，少一条就报 `stored log is corrupt`。
4. **完整性校验别只数文件。** 用「相对路径集合 + 逐文件 SHA-256 + 字节数 + mtime」四件套，再对 Office/PDF 做一次容器级校验（ZIP CRC、`%PDF`/`%%EOF`）。

## 目录

```
docs/
  01-workspace-registry.md   DSH 工作区注册表：位置、结构、加载时机、重启语义
  02-copy-and-verify.md      复制策略与四层完整性校验
  03-session-format.md       会话 v4 落盘格式：帧、projectKey、事件、三条硬校验
  04-pitfalls.md             踩坑清单（中文路径、编码、缓存、优雅退出、headless 凭据…）
  05-privacy.md              发布前的脱敏清单
scripts/
  discover_workspaces.py     从源工具数据库发现工作区（并集去重）
  copy_workspaces.py         按名称逐一复制，保留时间戳
  import_registry.py         写入工作区注册表（幂等）
  verify_integrity.py        四层完整性校验，输出报告
  check_deliverables.py      Office/PDF 容器级校验 + 产出物分类统计
  build_session_content.py   扫描工作区，生成"内容与产出物"清单正文
  write_sessions.py          按 v4 格式写会话日志并挂到工作区
  decode_session.js          多帧 zstd → JSONL（Node 一行解不开的坑）
  probe_session.ps1          用 `dsh headless --session-id` 做真实加载探针
  restart_dsh.ps1            脱离进程树的重启脚本（模板）
examples/
  workspace-manifest.example.json
  session-manifest.example.json
```

## 六步迁移流程

| 步骤 | 做什么 | 脚本 |
| --- | --- | --- |
| 1 | **发现**：从源工具数据库里取「工作区表 ∪ 全部本地会话 cwd」的并集，排除空目录与编码错乱产生的影子目录 | `discover_workspaces.py` |
| 2 | **备份**：先复制一份 `storages/workspace.json`，改坏了能回滚 | — |
| 3 | **复制**：按原名称复制到 DSH 工作区存储根目录（`<Documents>/deepseek-harness/<name>`） | `copy_workspaces.py` |
| 4 | **校验**：路径集合 / SHA-256 / 字节数 / mtime / 容器结构 | `verify_integrity.py`、`check_deliverables.py` |
| 5 | **注册**：写入注册表，`title` 用目录名，挂好 `sessionIds` | `import_registry.py` |
| 6 | **重启并验收**：硬终止 → 启动 → 检查 `<storages>/workspace/workspaces/*.json` 是否落地 | `restart_dsh.ps1` |

可选第 7 步：给每个工作区写一条会话（见 [03-session-format](docs/03-session-format.md)）。

## 快速开始

```bash
# 1) 发现工作区（源工具用 SQLite 记录窗口/会话）
python scripts/discover_workspaces.py \
  --db "$HOME/.workbuddy/workbuddy.db" \
  --source-root "$HOME/WorkBuddy" \
  --out workspaces.json

# 2) 复制（保留 mtime；空工作区也会创建空目录）
python scripts/copy_workspaces.py \
  --manifest workspaces.json \
  --source-root "$HOME/WorkBuddy" \
  --dest-root "$HOME/Documents/deepseek-harness"

# 3) 校验
python scripts/verify_integrity.py --manifest workspaces.json \
  --source-root "$HOME/WorkBuddy" --dest-root "$HOME/Documents/deepseek-harness" \
  --registry "$HOME/.dsh/storages/workspace.json" --out _migrate

# 4) 注册（幂等：同路径已存在则复用 id）
python scripts/import_registry.py --manifest workspaces.json \
  --dest-root "$HOME/Documents/deepseek-harness" \
  --registry "$HOME/.dsh/storages/workspace.json"
```

可选（给每个工作区配一条"内容与产出物"会话）：

```bash
python scripts/build_session_content.py --manifest workspaces.json \
  --dest-root "$HOME/Documents/deepseek-harness" \
  --registry "$HOME/.dsh/storages/workspace.json" --out session-manifest.json

node scripts/write_sessions.js --manifest session-manifest.json \
  --registry "$HOME/.dsh/storages/workspace.json" \
  --sessions-root "$HOME/.dsh/sessions"

# 用 DSH 自己的加载路径验收（不是"看起来对"）
pwsh scripts/probe_session.ps1 -SessionId session-xxxx
```

> `write_sessions.js` 需要 Node ≥ 22（`node:zlib` 的 zstd 支持）。整条链路已用隔离的
> `DSH_HOME` 做过端到端验证：生成的会话能被 `dsh headless --session-id` 正常解码。

> 所有脚本都显式传路径，不写死任何机器路径；`--help` 可查参数。

## 验收标准（建议照抄）

- `verify_integrity.py` 输出 `allOk: true`：缺失 0、多余 0、内容不一致 0、mtime 差异 0。
- 注册表里 `workspaceIds` 数量 = 迁移数 + 原有数，且 `tables.workspaces` 与之数量一致（顺序表必须覆盖全部记录，否则服务启动会抛不一致错误）。
- 重启后 `<storages>/workspace/workspaces/` 下的 `*.json` 数量与之相等——**这是"宿主真的加载了"最硬的证据**。
- 会话：`probe_session.ps1` 对每个 session 跑一遍，错误必须是"预设不支持"这类无关数据的提示，而不是 `corrupt`。

## 隐私

本仓库所有路径、工作区名称、项目名、账号信息均已替换为占位符；脚本不内置任何个人数据。发布前请按 [docs/05-privacy.md](docs/05-privacy.md) 自查。

## 许可

MIT，见 [LICENSE](LICENSE)。

---

## English summary

A field guide + toolkit for migrating agent workspaces into DeepSeek Harness: copy files, register workspaces in `~/.dsh/storages/workspace.json`, verify byte-identical copies with SHA-256, and (optionally) materialize a per-workspace session in the v4 `session.v4.jsonl.zstd` format.

Three things that cost the most time: (1) the workspace registry is read **once at process start**, so a restart is mandatory; (2) a graceful app quit **flushes the stale in-memory registry over your file** — kill the process instead; (3) v4 sessions require `message.source`, a `surfaceOp` marker and a `stream` array on assistant messages, otherwise the log is rejected as corrupt.
