# 04 · 踩坑清单

按"会浪费多少时间"排序。

## 1. 优雅退出会覆盖你的迁移结果（最坑）

**现象**：注册表里明明写了 12 个工作区，重启后只剩 1 个。

**原因**：宿主退出时把内存态刷回磁盘，而内存态是迁移前的旧表。

**对策**：迁移场景一律强制终止（`Stop-Process -Force`），不要发 `WM_CLOSE`、不要走菜单退出。

**代价**：可能丢最后几秒未落盘的会话日志；迁移时通常可接受。

---

## 2. 改完注册表必须重启，运行中不会生效

`Service.init` 里一次性读入 `this.state` / `this.table`，之后全用内存态。改文件后不重启，侧边栏永远是旧的。

---

## 3. PowerShell 5.1 把无 BOM 的 UTF-8 脚本当 ANSI 读

**现象**：脚本里的中文目录名变成乱码（如 `示例目录` → 一串 `绀轰緥鐩綍` 式乱码），复制到了错误的目录；更隐蔽的是**恰好命中源目录里一个编码损坏的同名影子目录**，于是"复制成功"但内容是错的。

**原因**：`powershell.exe`（5.1）默认按当前 ANSI 代码页解释无 BOM 文件；`pwsh` 7 才是 UTF-8。

**对策**：
- 涉及非 ASCII 路径的批量操作，用 **Python**（显式 `encoding='utf-8'`）；
- 必须用 PowerShell 时，脚本存为 **UTF-8 with BOM**，或用 `-EncodedCommand` 传 Base64(UTF-16LE)。

---

## 4. 执行策略禁止 `.ps1`

`无法加载文件 ... 因为在此系统上禁止运行脚本`。

**对策**：
- `powershell -ExecutionPolicy Bypass -EncodedCommand <b64>`（不需要改注册表）；
- 或 `[scriptblock]::Create((Get-Content -Raw x.ps1))` 再 `&`（注意第 3 条的编码陷阱）。

---

## 5. 用 `dsh headless` 生成会话会卡在凭据

**现象**：`MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"`。

**原因**：桌面应用用**账号登录**（凭据在凭据服务里，是 account-platform token），而 headless profile 走的是 API Key 路线。

**对策**：
- 要么配置 `DEEPSEEK_API_KEY` 让 headless 可用；
- 要么不要用 headless 造会话，直接按 [03](03-session-format.md) 写日志；
- headless 仍然值得用：**`--session-id` 是现成的"格式探针"**。

---

## 6. zstd 多帧：一行解不开

`zstdDecompressSync` / `createZstdDecompress` 都只解第一帧，读到 244 字节就停，容易误判文件损坏。必须扫描帧边界逐帧解（实现见 `scripts/decode_session.js`）。

---

## 7. 会话 v4 的三条硬校验

`message.source`、`surfaceOp`、`data.stream`——见 [03](03-session-format.md)。逐条撞出来的，报错信息很具体，照着补即可。

---

## 8. 注册表一致性是"抛错"不是"修正"

`workspaceIds` 与 `tables.workspaces` 数量不一致、路径重复、会话归属重复——任意一条都会让服务启动失败。批量写入前先自检这五条（见 [01](01-workspace-registry.md)）。

---

## 9. 重启脚本把自己也杀了

如果你在 DSH 的 agent 会话里执行重启，`Stop-Process` 会连带杀掉承载自己的宿主进程，turn 直接中断。

**对策**：用 WMI `Win32_Process.Create` 拉起独立进程（父进程是 `WmiPrvSE`，不在被杀的树里），并 `Start-Sleep 20` 让消息先落盘。已在 `scripts/restart_dsh.ps1` 里给出模板。

---

## 10. 影子目录 / 编码错乱目录不是工作区

源工具可能因为历史编码 bug 留下形似但乱码的目录（只含 1 个 66 字节的临时状态文件），同时真实目录内容完整。

**对策**：以**源工具的数据库**为准枚举工作区（`workspaces` 表 ∪ 本地会话 `cwd`），不要用"扫目录"当权威；对无法匹配到任何会话/工作区记录的目录，先检查再决定是否排除。

---

## 11. 文件数对不上 ≠ 复制失败

`os.walk` 会跟进联接点、`Thumbs.db` 会随浏览生成。别用总数当唯一指标，用**路径集合 + 哈希**。

---

## 12. 用户手动删了工作区，注册表会立刻重写

迁移后如果发现"12 变成 9"，先确认不是用户自己删的——宿主在删除操作时会用内存态重写整个注册表。**排查时先看 mtime 与用户操作时间线，别急着怀疑格式 bug。**

---

## 13. Windows PowerShell 写的 UTF-8 带 BOM

`Set-Content -Encoding UTF8`（5.1）会加 BOM，`json.load` / `JSON.parse` 直接抛
`Unexpected token '\uFEFF'`。

**对策**：读 JSON 一律容忍 BOM——Python 用 `encoding='utf-8-sig'`，Node 读入后剥掉首字符；
写文件时用无 BOM 的 `[Text.UTF8Encoding]::new($false)`。

```js
let text = fs.readFileSync(file, 'utf8');
if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
```

---

## 14. 用 PowerShell 拼测试数据时，单元素数组会被展平成对象

`@(@{...}) | ConvertTo-Json` 得到的是**对象**而不是 `[...]`，脚本里 `for (const x of manifest)`
就报 `not iterable`。手工写 JSON 文本，或 `ConvertTo-Json -InputObject @($arr)` 并检查输出。

