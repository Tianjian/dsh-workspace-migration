# 02 · 复制与完整性校验

## 复制：为什么最后选了 Python 而不是 robocopy

| 方案 | 优点 | 问题 |
| --- | --- | --- |
| `robocopy /E /COPY:DAT /DCOPY:DAT` | 稳、快、长路径友好 | 参数为中文路径时若脚本文件编码不对会传错；退出码需要解释（0–7 都算成功） |
| PowerShell `Copy-Item` | 简单 | 不保留 mtime；长路径受限 |
| Python `shutil.copytree(copy_function=shutil.copy2)` | UTF-8 路径天然正确、保留 mtime、`dirs_exist_ok` 幂等 | 需自己收集错误 |

最终方案：**Python + `shutil.copy2`**，理由是"名称必须逐字一致"——中文与空格路径不能有任何编码损耗。

```python
shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=shutil.copy2,
                ignore_dangling_symlinks=True)
```

要点：

- `dirs_exist_ok=True` 让脚本可重复执行；
- 空工作区也要 `os.makedirs(dst, exist_ok=True)`，保留"这个工作区存在"这一事实；
- 不要过滤隐藏目录（`.workbuddy/`、`.cache/`、`.slidep/` 里有过程素材，属于"工作区内容"的一部分）。

## 校验：四层

### 1. 路径集合

递归枚举源与目标（**包含空目录**），比较相对路径集合：

```
missing = src_paths - dst_paths
extra   = dst_paths - src_paths
```

只要其中一个不为空，就说明复制不完整或有污染。

### 2. 逐文件 SHA-256

对每个文件算摘要，逐个比较。**全量，不抽样**。1 GB 级别通常十几秒到一分钟。

```python
def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
```

### 3. 字节数 + mtime

- 字节数：`sum(size)` 两侧相等；
- mtime：`abs(src_mtime - dst_mtime) <= 2s` 视为一致（不同文件系统的精度差异）。

实测 10,322 个文件 mtime 差异为 0。

### 4. 容器级结构校验（"产出物完整性"）

哈希相同只说明"复制没坏"，不说明"源文件本身可用"。对关键产出物再做一次结构校验：

| 类型 | 方法 |
| --- | --- |
| `.docx` `.pptx` `.xlsx` | 本质是 OPC ZIP：`zipfile.ZipFile(...).testzip()` 读全部 CRC |
| `.pdf` | 头 `%PDF`，尾 2 KB 内出现 `%%EOF` |
| 图片 | 可读性检查（Pillow，可选） |

一次真实案例：30 个 pptx 全部通过 CRC，17 个 PDF 头尾正常，问题数 0。

## 报告

`verify_integrity.py` 输出两份：

- `integrity-report.json`：逐工作区明细（文件数、字节数、缺失/多余/不一致计数、问题列表）；
- `integrity-report.md`：人看的汇总表 + 总计行。

```markdown
| workspace | files | MB | registered | title | verdict |
| --- | ---: | ---: | :---: | --- | --- |
| alpha | 1379 | 75.56 | yes | yes | PASS |
...
Totals: 11 workspaces, 11 passing, 0 failing, 10322 files, 1222.80 MB, allOk=True
```

## 常见误报

- **符号链接/联接点**：`os.walk` 默认会跟进，可能重复计数。用 `os.lstat` 判断并跳过，或 robocopy `/XJ`。
- **系统缩略图缓存**（`Thumbs.db`、`desktop.ini`）：如果源目录被资源管理器打开过，可能生成；复制后两侧都有则不影响一致性，但会污染"产出物"统计，建议在分类统计里排除。
- **mtime 精度**：FAT/exFAT 只到 2 秒，用容差比较。
