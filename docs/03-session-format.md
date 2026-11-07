# 03 · 会话 v4 落盘格式

工作区注册好之后，侧边栏能看到工作区，但点进去是空的。会话（Session）是**独立**的日志文件。

## 路径

```
$DSH_HOME/sessions/<projectKey>/<sessionId>/session.v4.jsonl.zstd
```

### projectKey 编码

把会话的 `cwd` 变成一个可读的目录名：

1. `/` `\` `:` 的连续串 → 一个 `-`；
2. `[A-Za-z0-9._-]` 原样保留；
3. 其他字符（含中文、空格、`~`）→ `~` + 4 位大写十六进制（按 UTF-16 code unit）；
4. 清理开头多余的 `-`，截断到 251 字符，两侧包 `--`。

```
cwd:  C:\Users\me\Documents\deepseek-harness\Demo Workspace
dir:  --C-Users-me-Documents-deepseek-harness-Demo~0020Workspace--
cwd:  C:\Users\me\Documents\deepseek-harness\示例工作区
dir:  --C-Users-me-Documents-deepseek-harness-~793A~4F8B~5DE5~4F5C~533A--
```

`<sessionId>` 也走同样规则（`session-<uuid>` 全是安全字符，通常不变）。

## 容器：连续 zstd 帧

不是"一个 zstd 压缩的 JSONL"，而是**多个可独立解码的 zstd 帧拼接**：

| 帧 | 内容 |
| --- | --- |
| 第 1 帧 | **恰好一行** header（含末尾换行） |
| 第 2..n 帧 | 每帧一批事件行（JSONL） |

每帧带 checksum。追加写就是"再拼一个帧"，所以读取时必须**按帧边界解**。

### 坑：Node 的 zstd API 只解第一帧

```js
zlib.zstdDecompressSync(buf)      // → 只有第 1 帧（header），后面全丢
zlib.createZstdDecompress()       // → 同样在帧边界停下
```

表现为"读到 1 行、244 字节"，让人误以为文件坏了。正确做法是**先扫描帧边界，再逐帧解码**：

```js
// RFC 8878 帧头解析（简化）
magic(4) = 0xFD2FB528
descriptor(1): fcsFlag=>>6, singleSegment=>>5&1, checksum=>>2&1, dictIdFlag=&3
if (!singleSegment) windowDescriptor(1)
dictIdLen = [0,1,2,4][dictIdFlag]
fcsLen = fcsFlag===0 ? (singleSegment?1:0) : fcsFlag===1?2 : fcsFlag===2?4 : 8
then blocks: 3 字节头 = last(bit0) | type(bits1-2) | size(bits3-23)
  type 0/2 → 跳过 size 字节；type 1(RLE) → 跳过 1 字节
带 checksum 时再加 4 字节
```

完整实现见 [`scripts/decode_session.js`](../scripts/decode_session.js)。

> 写文件时用 `zlib.zstdCompressSync(buf, { params: { [zlib.constants.ZSTD_c_checksumFlag]: 1 } })`，
> 与官方写入保持一致（帧带校验和）。

## 事件模型

一行一个事件：

```json
{ "type": "user/message", "seq": 4, "time": 1794006710382, "data": {...}, "surfaceOp": "append" }
```

- `seq`：单调递增的非负整数（不要求连续，但必须递增）；
- `time`：毫秒时间戳；
- `data`：按事件类型各自定义；
- `surfaceOp`：**消息类事件必带**。

### header（第 1 帧）

```json
{"type":"session","version":4,"id":"session-<uuid>","createdAt":1794006710375,
 "cwd":"C:\\path\\to\\workspace","isSeeded":false,"delegationDepth":0,"agentPreset":"standard"}
```

`version`/`id`/`createdAt`/`isSeeded`/`delegationDepth` 是必填项。

### 最小可用事件序列

```json
{"type":"permission/preset","seq":0,"time":...,"data":{"preset":"workspace-write"}}
{"type":"approval/policy","seq":1,"time":...,"data":{"policy":"never"}}
{"type":"turn/start","seq":2,"time":...,"data":{"turn":1}}
{"type":"step/start","seq":3,"time":...,"data":{"turn":1,"step":1}}
{"type":"user/message","seq":4,"time":...,"surfaceOp":"append","data":{
   "content":[{"type":"text","text":"..."}],
   "source":{"kind":"user","rpcId":"<uuid>","clientTimeZone":"Asia/Shanghai"},
   "role":"user","id":"<uuid>"}}
{"type":"session/title","seq":5,"time":...,"data":{"title":"...","messageSeqs":[4],"source":{"kind":"fallback"}}}
{"type":"assistant/message","seq":6,"time":...,"surfaceOp":"append","data":{
   "turn":1,"step":1,"stream":[],
   "message":{"role":"assistant","content":[{"type":"text","text":"..."}],
              "source":{"kind":"model","provider":"<provider>","model":"<model>"},"id":"<uuid>"}}}
{"type":"step/end","seq":7,"time":...,"data":{"turn":1,"step":1}}
{"type":"turn/end","seq":8,"time":...,"data":{"turn":1,"reason":{"kind":"completed"}}}
```

## 三条硬校验（最容易漏，逐条撞出来的）

| # | 报错 | 原因 | 修法 |
| --- | --- | --- | --- |
| 1 | `format v4 message requires a producer-owned source kind` | `assistant/message` 的消息缺 `source`；v4 要求每条持久化消息都带"产生者来源"，且 `kind` 不能是 `plugin` | assistant 用 `{"kind":"model","provider":...,"model":...}`；user 用 `{"kind":"user","rpcId":...,"clientTimeZone":...}` |
| 2 | `assistant/message surfaceOp requires an object`（实际是缺少标记） | 消息类事件必须带 `surfaceOp` 标记 | `"surfaceOp": "append"`（或 `{"op":"replace","startSeq":n,"endSeq":m}`） |
| 3 | `seed assistant/message at index N has invalid settlement fields` | `assistant/message` 的 `data` 缺 `stream` 数组（会话生命周期逻辑直接读它） | 补 `"stream": []` |

校验逻辑分别在 `@deepseek-ai/dsh-session-format-v3-to-v4`（消息来源、退役语法）和 `@deepseek-ai/dsh-session`（`turn`/`step`/`stream` 的 settlement 形状）。

## 怎么验证"真的能打开"

不要靠"看起来对"。DSH 自带一个真实加载路径：**`dsh headless --session-id <id>`** 会解码目标会话以便续跑。

```powershell
dsh headless --session-id session-xxxx "x"
```

- 有格式问题 → `stored session "..." is corrupt: ... SessionFormatError: ...`（错误文本会精确到哪条校验）；
- 格式正确 → 会走到下一步，例如 `runs under agent preset "standard", which the one-shot runner does not compose`。

**后者才是通过。** 这条信息与数据无关，是 headless 一次性运行器的限制。

## 注册到工作区

会话文件写完还不够，必须把 `sessionId` 放进对应工作区记录的 `sessionIds` 数组（见 [01](01-workspace-registry.md)）。否则文件存在但侧边栏不归位。

同时注意 `cwd` 必须与工作区 `path` 逐字一致（realpath 规范后的形式），否则启动时会打日志 `filtered session ... from membership: canonical cwd ... differs`，会话被从成员关系里过滤掉。

## 关于"伪造历史"的边界

本仓库的会话是**只读快照**：有问、有答、有标题，但没有真实的工具调用与模型元数据（`request/header`、`usage` 等）。它适合"给每个工作区配一份内容清单"这种展示型需求。

如果你要的是"能继续对话"的会话，请不要伪造——让 agent 真实跑一次（DSH CLI 的 `dsh headless`，需要配置模型 API Key），由宿主自己写日志。
