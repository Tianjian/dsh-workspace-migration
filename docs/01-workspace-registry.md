# 01 · DSH 工作区注册表

## 位置

```
$DSH_HOME/storages/workspace.json      # 旧版"整包"布局（单文件）
$DSH_HOME/storages/workspace/          # 新版"按记录拆分"布局（目录）
    global.json
    workspaces/<workspaceId>.json
```

`$DSH_HOME` 默认 `~/.dsh`（Windows：`C:\Users\<你>\.dsh`）。

两种布局的关系：**启动时若 `workspace/` 目录不存在，宿主会把 `workspace.json` 里的记录迁移成按记录拆分的文件**，之后以目录为准。所以"往 `workspace.json` 里写"在首次启动前有效，之后就该直接写目录里的记录文件。

> 实测：迁移后第一次重启，`workspace/` 目录仍然不存在（说明该版本尚未物化新布局），于是每次启动都从 `workspace.json` 读取——这也是为什么第一次写 `workspace.json` 就够了。

## 结构（域 `workspace`，version 2）

```json
{
  "unit":  { "name": "workspace", "version": 2 },
  "global": {
    "initialized": true,
    "workspaceIds": ["<id-1>", "<id-2>"],
    "archivedSessionIds": [],
    "pinnedSessionIds": [],
    "defaultWorkspaceId": "<id-1>"
  },
  "tables": {
    "workspaces": {
      "<id-1>": {
        "path": "C:\\Users\\me\\Documents\\deepseek-harness\\default-workspace",
        "title": "DSH",
        "sessionIds": ["session-..."],
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z"
      }
    }
  }
}
```

字段含义（来自 `@deepseek-ai/dsh-workspace` 的 zod schema）：

| 字段 | 说明 |
| --- | --- |
| `path` | `fs.realpath` 规范化的绝对路径，**同一路径只能属于一个工作区** |
| `title` | 侧边栏展示名；与目录名一致最省心 |
| `sessionIds` | 该工作区下的会话，**数组顺序即展示顺序**；一个会话只能归属一个工作区 |
| `createdAt` / `updatedAt` | ISO-8601 字符串 |
| `global.workspaceIds` | **权威展示顺序**，必须与 `tables.workspaces` 的 key 集合完全一致 |

## 启动时会做的一致性校验

宿主启动时执行 `validateStoredState()`，以下任一情况都会**抛错中断**（不是静默丢弃）：

- `workspaceIds` 里有重复 id；
- `workspaceIds` 引用了不存在的记录；
- `initialized === true` 时，`workspaceIds` 的数量少于表的记录数（"有记录没进顺序表"）；
- 同一条 `path` 被两个工作区声明；
- 同一个 `sessionId` 出现在两个工作区的 `sessionIds` 里。

所以批量改注册表时，**先自检这五条**，否则一次重启就白屏/报错。

## 加载时机：只在启动时读一次

```js
// 简化自 @deepseek-ai/dsh-workspace 的 Service.init
async [Service.init]() {
  const domain = await this.ctx.storageDomain.open(workspaceDomainSpec);
  this.table  = domain.table('workspaces');
  this.state  = domain.global.get();      // ← 内存快照
  this.validateStoredState(this.state);
  ...
  this.rebuildEntities();
}
```

之后所有列表/排序/新增都基于 `this.state` 与 `this.table`（内存态）。**运行中修改磁盘文件不会生效，也不会被感知。**

## 由此推导出的两条操作规则

### 规则一：改完必须重启

`workspace.json` 的正确性只影响"下一次启动"。

### 规则二：重启必须是硬终止

宿主退出时会走关闭流程，其中包含把内存态**刷回磁盘**的动作。此时内存里是"迁移前"的旧表——一次优雅退出就会把刚写进去的 N 个工作区抹掉。

实测日志（脱敏）：

```
workspaceIds before restart: 12      ← 磁盘上写对了
[graceful quit]
per-record unit dir still missing
legacy workspaceIds after restart: 1 ← 被内存态覆盖
```

改成强制终止（`Stop-Process -Force`，不走 `WM_CLOSE`）后：

```
workspaceIds before restart: 12
[force kill]
legacy workspaceIds after restart: 12
```

> 取舍：强制终止可能丢掉最后几秒尚未落盘的会话日志。迁移场景下这个代价可以接受；生产环境请自行评估。

## 重启脚本为什么要"脱离进程树"

如果你是在 DSH *内部*的 agent 会话里执行重启（比如让 AI 自己重启 DSH），重启会杀掉承载你自己的进程。做法：

1. 用 WMI 创建进程（父进程是 `WmiPrvSE`，不在被杀的树里）：
   ```powershell
   Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
     CommandLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand <base64>"
     CurrentDirectory = "<repo>"
   }
   ```
2. 脚本里先 `Start-Sleep 20`，让当前对话的消息先落盘；
3. 再杀进程 → 等待退出 → 重新启动；
4. 最后自己校验并写日志。

已验证：WMI 创建的进程落在**当前交互会话**（`SessionId` 相同），所以重新拉起的 GUI 窗口可见。

## 如何确认"宿主真的加载了"

看 `<storages>/workspace/workspaces/*.json`：

- 目录不存在 = 宿主还没物化新布局（仍然读 `workspace.json`）；
- 目录里出现 N 个 `*.json` = 宿主读过并写回，N 应等于工作区数。

这是比"侧边栏看起来有"更可靠的程序化证据。
