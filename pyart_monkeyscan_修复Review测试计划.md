# Py-ART（arm-pyart）安全漏洞修复 Review 与测试计划

> 输入材料：
> - `monkeyscan-task-Circumsized_arm-pyart-cinrad_main-report.csv`（44 条缺陷汇总表）
> - `monkeyscan-task-Circumsized_arm-pyart-cinrad_main-defects.zip`（44 份独立缺陷详报）
>
> 评审对象：ARM-DOE/pyart（PyPI: arm-pyart，BSD-3-Clause）main 分支
> 文档性质：漏洞归因梳理 + 修复方案设计 + 修复 Review 流程 + 验证测试计划
> 编制日期：2026-09-19

---

## 1. 执行摘要

本次评审的扫描报告共提出 **44 项缺陷**（严重程度：致命 1 / 高危 6 / 中危 24 / 低危 13；AI 验证状态：已确认且已验证 32、仅初筛 12）。经逐项归因分析，确认报告整体可信度高：绝大多数结论均可由源码证据链（.pyx/.py/.c 源码、生成的 C 代码、调用链、公开入口）闭环支撑，且给出了具体修复方向。

核心判断：

1. **这不是零散 bug 的集合，而是三类系统性缺陷的集中显形**：
   - 信任边界缺失——所有二进制/NetCDF/HDF5 解析器把文件头字段直接当作可信输入（占 24 项，全部集中于 `pyart/io/`，占全部缺陷的 54.5%）；
   - 性能优化覆写安全不变量——Cython `boundscheck(False)/wraparound(False)` 被广泛用作默认性能手段，但函数契约（允许空数组、ng<3 等）未同步保证（内存安全类 18 项，集中于 `pyart/correct/`、`pyart/retrieve/`、`pyart/map/`）；
   - 危险 API 与可被短路的安全控制——`os.system` 拼接文件名（RCE）、`allow_private=True` 使 SSRF 防护空转、`exec_module` 加载配置即执行代码。
2. **必须优先处置的 7 个独立漏洞（8 个 ID）**：OS 命令注入（ed8eb，致命）、SSRF×2（e690e/d9214）、Sigmet 负 nbins 堆越界写（6ff3b）、KDP 两个 OOB 读（21f73/29123）、cKDTree 双重释放（b2f85/c004f 为同一缺陷的两份报告）、路径穿越（94f00）。
3. **修复必须"代码 + 测试 + 样本"三件套同步提交**：任何仅改代码不附回归测试与恶意样本 PoC 的修复一律不予合入；涉及 `.pyx` 的修改必须重新生成 `.c` 并做 ASan 验证。

建议按 P0（1 周）/ P1（3 周）/ P2（6 周）三波推进，全部完成后以第 9 章验收标准逐条核销。

---

## 2. 评审方法与依据

### 2.1 材料处理

- CSV 44 条全量解析；ZIP 内 44 份详报全量解包抽查（覆盖致命/高危全量与各中危类别代表 12 份），确认报告结构一致（总结→分析逻辑→结论→验证复现→修复建议五段式）。
- 对疑似重复/耦合项做并案：b2f85 与 c004f 为 `ckdtree.pyx:965-971` 同一双重释放缺陷的两份报告（定级不一致，建议按高危取 b2f85 为准）；3b55a 与 89582 为 NEXRAD 插值门数失配问题的上下游两侧（`nexrad_archive.py` 调用侧与 `nexrad_interpolate.pyx` 内核侧），应作为一个修复单元实施。

### 2.2 权威依据（联网检索）

| 领域 | 权威来源 | 对本报告的直接指导 |
|---|---|---|
| Cython 安全编程 | Cython 官方文档（boundscheck/wraparound 指令语义）、scikit-learn Cython 最佳实践（`SKLEARN_ENABLE_DEBUG_CYTHON_DIRECTIVES`） | `boundscheck(False)/wraparound(False)` 下的所有索引必须有显式前置校验；调试期用开启 boundscheck 的构建排查 OOB |
| SSRF 防护 | OWASP Server-Side Request Forgery Prevention Cheat Sheet | 禁止黑名单式主机名校验；必须 DNS 解析后按 IP 分类拒绝私有/回环/链路本地/元数据地址，且重定向后重新校验 |
| C 安全编码 | SEI CERT C（INT04-C、INT30-C、INT31-C、INT32-C、MEM30-C、MEM34-C、EXP34-C、FIO34-C） | 来自不可信源的整数必须强制范围限制；分配前做溢出检查；`calloc`/`malloc` 返回值必须判空；`free` 必须一次且唯一 |
| 路径穿越 | CWE-22 修复指南（Canonicalize + Containment）、OWASP | 输出路径必须 `realpath/resolve` 后做目录包含性校验，而非事后清理字符串 |
| 解压炸弹/资源耗尽 | CWE-409/CWE-400/CWE-789、aiohttp CVE-2025-69223 修复（32 MiB 解压上限）、CPython CVE-2026-15310（bzip2/LZMA 预分配上限） | 流式解压 + 输出上限 + 比率阈值，超限即中止 |
| 可达断言 | CWE-617 | 不得用 `assert` 做不可信输入校验（`python -O` 下被剥离） |
| 模糊测试 | OSS-Fuzz / Atheris（Python 覆盖引导模糊测试） | 为 `pyart.io` 解析器建 Atheris harness，畸形样本回归语料库沉淀 |

---

## 3. 漏洞全景

### 3.1 统计

- 按严重程度：致命 1（2.3%）、高危 6（13.6%）、中危 24（54.5%）、低危 13（29.5%）
- 按 AI 验证：已确认/已验证 32（72.7%）、仅初筛/未验证 12（27.3%）
- 按模块：`pyart/io` 24、`pyart/aux_io` 7、`pyart/map` 5、`pyart/correct` 4、`pyart/retrieve` 2、`pyart/graph` 1、`pyart/config` 1

### 3.2 缺陷总表（44 项，含根因归类与修复优先级）

> RC 编号见第 4 章根因模型；优先级见第 6 章。"修复单元"列中 FU-x 为并案后的独立修复单元编号（共 42 个）。

| # | 缺陷ID | 级别 | 位置 | 类型/CWE | 状态 | RC | 修复单元 | 优先级 |
|---|---|---|---|---|---|---|---|---|
| 1 | ed8eb0 | 致命 | pyart/io/output_to_geotiff.py | OS 命令注入 CWE-78 | 已验证 | RC-6 | FU-01 | P0 |
| 2 | e690e4 | 高危 | pyart/io/remote.py | SSRF 防护被显式关闭 CWE-918 | 已验证 | RC-5 | FU-02 | P0 |
| 3 | d9214c | 中危 | pyart/io/remote.py | SSRF 防护被 DNS 主机名绕过 CWE-918 | 已验证 | RC-5 | FU-02 | P0 |
| 4 | 6ff3be | 高危 | pyart/io/_sigmetfile.pyx | 堆越界写（负 nbins）CWE-787 | 已验证 | RC-1/RC-2 | FU-03 | P0 |
| 5 | 21f732 | 高危 | pyart/retrieve/_kdp_proc(.pyx/.c) | 堆越界读 CWE-125 | 已验证 | RC-2 | FU-04 | P0 |
| 6 | 291237 | 高危 | pyart/retrieve/_kdp_proc(.pyx/.c) | 堆越界读 CWE-125 | 已验证 | RC-2 | FU-04 | P0 |
| 7 | b2f856 (+c004f5) | 高/中 | pyart/map/ckdtree(.pyx/.c) | 双重释放 CWE-415 + ni 泄漏 | 已验证 | RC-7 | FU-05 | P0 |
| 8 | 94f00e | 高危 | pyart/graph/max_cappi.py | 路径穿越 CWE-22 | 已验证 | RC-1/RC-6 | FU-06 | P0 |
| 9 | c5ca90 | 中危 | pyart/correct/_unwrap_1d(.pyx/.c) | 空缓冲区堆越界读写 CWE-787 | 已验证 | RC-2 | FU-07 | P1 |
| 10 | 1a7f21 | 中危 | pyart/correct/unwrap_3d_ljmu.c | calloc 未判空解引用 CWE-476 + int 溢出 | 已验证 | RC-6/RC-3 | FU-08 | P1 |
| 11 | 7b4e3f | 中危 | pyart/io/_sigmetfile.pyx | 压缩游程长度未掩码导致堆越界读 CWE-125 | 已验证 | RC-1/RC-3 | FU-09 | P1 |
| 12 | 3b55ab + 89582b | 中/低 | pyart/io/nexrad_archive.py + nexrad_interpolate.pyx | 门数失配致越界索引（现为 IndexError） | 已验证+初筛 | RC-1/RC-2 | FU-10 | P1 |
| 13 | 4f04a8 | 中危 | pyart/map/_gate_to_grid_map(.pyx/.c) | h_factor 分量越界读 CWE-125 | 已验证 | RC-2 | FU-11 | P1 |
| 14 | a8abf7 | 中危 | pyart/map/ckdtree(.pyx/.c) | 空堆 remove 下溢读 + k=0 越界写 | 已验证 | RC-2/RC-7 | FU-12 | P1 |
| 15 | d7c300 | 中危 | pyart/io/mdv_common.py | RLE8 解码越界读 CWE-125/131 | 已验证 | RC-1 | FU-13 | P1 |
| 16 | d2238d | 中危 | pyart/io/_sigmetfile.pyx | 文件头维度驱动无界分配 CWE-789 | 已验证 | RC-1/RC-3 | FU-14 | P1 |
| 17 | b94672 | 中危 | pyart/io/mdv_common.py | 字段头维度驱动无界分配 CWE-789 | 已验证 | RC-1/RC-3 | FU-15 | P1 |
| 18 | 18f97e | 中危 | pyart/io/nexrad_level3.py | 径向/门数无界分配 + 静默截断 CWE-789/131 | 已验证 | RC-1/RC-3 | FU-16 | P1 |
| 19 | 8227a7 | 中危 | pyart/io/nexrad_level3.py | 空 radials 下标 + 无界分配 + 未校验包头切片 | 已验证 | RC-1/RC-3 | FU-17 | P1 |
| 20 | f56fc4 | 中危 | pyart/io/nexrad_level2.py | bz2 无界解压（解压炸弹）CWE-409/400 | 已验证 | RC-1 | FU-18 | P1 |
| 21 | e756f4 | 中危 | pyart/io/C98DRadFile.py | 有符号长度驱动切片/游标 CWE-131/835 | 已验证 | RC-1 | FU-19 | P1 |
| 22 | 7a67cb | 中危 | pyart/io/cfradial.py | ray_n_gates/ray_start_index 未校验致 IndexError | 已验证 | RC-1 | FU-20 | P1 |
| 23 | 434d5a | 中危 | pyart/aux_io/rxm25.py | NetCDF 维度驱动无界分配 CWE-789 | 已验证 | RC-1/RC-3 | FU-21 | P1 |
| 24 | 17bab9 | 中危 | pyart/aux_io/rxm25.py | 变量 shape 与 Radar 维度不校验 | 已验证 | RC-1 | FU-22 | P1 |
| 25 | 57a9dd | 中危 | pyart/aux_io/gamicfile.py | HDF5 'sets' 属性驱动无界列表分配 CWE-789 | 已验证 | RC-1/RC-3 | FU-23 | P1 |
| 26 | db0886 | 中危 | pyart/aux_io/d3r_gcpex_nc.py | NumGates 驱动无界分配 CWE-789 | 已验证 | RC-1/RC-3 | FU-24 | P1 |
| 27 | 9b7666 | 中危 | pyart/io/sband_radar.py | ngates 无钳制赋值致 ValueError | 已验证 | RC-4 | FU-25 | P1 |
| 28 | c73c45 | 中危 | pyart/io/nexrad_level2.py | MSG31 名称 ASCII 解码 + 指针未校验 | 已验证 | RC-1/RC-9 | FU-26 | P2 |
| 29 | f89e9e | 中危 | pyart/io/nexrad_level2.py | MSG31 size/block_pointer 未校验致 struct.error | 已验证 | RC-1/RC-3 | FU-27 | P2 |
| 30 | 86ab7a | 中危 | pyart/io/sband_archive.py | assert 做输入校验（-O 下失效）CWE-617 | 已验证 | RC-8 | FU-28 | P2 |
| 31 | 854841 | 低危 | pyart/correct/_unwrap_2d(.pyx/.c) | Py_ssize_t→int 截断 CWE-681 | 初筛 | RC-2/RC-3 | FU-29 | P2 |
| 32 | fae334 | 低危 | pyart/correct/src/dealias_fourdd.c | 负射线索引解引用 CWE-125 | 初筛 | RC-2 | FU-30 | P2 |
| 33 | bf4710 | 低危 | pyart/io/xband_native.py | np.empty 未写入行泄露堆内存 CWE-908 | 初筛 | RC-1 | FU-31 | P2 |
| 34 | 0e8f8c | 低危 | pyart/map/_gate_to_grid_map(.pyx/.c) | float→int 越界转换（UB）+ step=0 坍缩 CWE-681 | 初筛 | RC-2/RC-3 | FU-32 | P2 |
| 35 | 268283 | 低危 | pyart/io/sigmet.py | dict_keys 下标 TypeError（健壮性） | 初筛 | RC-4 | FU-33 | P2 |
| 36 | 5f2e4b | 低危 | pyart/io/sigmet.py | number_bins=0 除零/reshape 失败 | 初筛 | RC-1 | FU-34 | P2 |
| 37 | 107f50 | 低危 | pyart/io/uffile.py | 有符号记录长度导致整文件缓冲 | 初筛 | RC-1 | FU-35 | P2 |
| 38 | 376b4c | 低危 | pyart/io/chl.py | 有符号块长导致整文件缓冲 + 游标错乱 | 初筛 | RC-1 | FU-36 | P2 |
| 39 | 23c53c | 低危 | pyart/aux_io/kazr_spectra.py | 正则结果空下标 IndexError | 初筛 | RC-4 | FU-37 | P2 |
| 40 | 7dc6bf | 低危 | pyart/aux_io/sinarame_h5.py | 非数字组名 int() ValueError | 初筛 | RC-4 | FU-38 | P2 |
| 41 | e3e000 | 低危 | pyart/config.py | 配置文件加载即执行任意代码 CWE-94/95 | 初筛 | RC-6 | FU-39 | P2 |
| 42 | 3fbfe4 | 低危 | pyart/aux_io/radx.py | fd 泄漏 + CWD 建临时文件（TOCTOU）CWE-403/377 | 初筛 | RC-6 | FU-40 | P2 |
| 43 | c004f5 | 中危 | pyart/map/ckdtree.pyx | （同 b2f856，双重释放） | 已验证 | RC-7 | 并入 FU-05 | — |
| 44 | 89582b | 低危 | pyart/io/nexrad_interpolate.pyx | （同 3b55ab，插值门数失配） | 初筛 | RC-1/RC-2 | 并入 FU-10 | — |

---

## 4. 根因归因模型

### RC-1 信任边界缺失：文件内容即命令（涉及 24 项 io + 7 项 aux_io）

**机制**：所有解析器直接把文件头/属性中的整数（`number_bins`、`nrays`、`nsweeps`、`nx/ny/nz`、`NumGates`、`sets`、`number_bytes`、`record_length` 等）用作分配尺寸、循环边界、内存偏移与游标推进，没有任何一层执行"解析前校验"。这使单个畸形文件的成本从"解析失败"放大为"进程级资源耗尽或内存破坏"。

**权威依据**：CERT INT04-C（"对来自不可信源的整数值强制限制"）；CWE-131/CWE-789；CWE-409（aiohttp CVE-2025-69223 的解压上限修复即针对同一模式）。

**判定**：这是本报告中**唯一的系统性根因**，覆盖 31 项（FU-09/13~24、26~27、31、33~36、89482 侧等），必须用统一方案（见 5.1）根治，而非逐条打补丁。

### RC-2 性能注解覆写安全不变量（涉及 18 项内存安全类中的 9 项）

**机制**：`@cython.boundscheck(False)` / `@cython.wraparound(False)` 在 `pyart/correct`、`pyart/retrieve`、`pyart/map` 中被大量使用以追求数值计算性能。Cython 官方文档明确：wraparound=False 时负索引不再被正确回绕，"best case crash, worst case corrupt data"。缺陷函数的**参数契约**（空数组、ng=1/2、长度不足的 h_factor、空堆 remove）与**实现假设**（至少 1/3/4 个门、h_factor≥3、堆非空）不一致，且契约允许值全部可由不可信文件内容代入。

**权威依据**：Cython 官方文档；scikit-learn Cython 实践（提供 `SKLEARN_ENABLE_DEBUG_CYTHON_DIRECTIVES` 开关以便在调试构建中恢复 boundscheck）。

**判定**：修复顺序必须是"先在 Python/Cython 层显式收紧契约并抛 `ValueError`，再考虑保留 boundscheck=False 性能"；不得简单删除注解了事（会引入性能回退，遭维护者抵制），也不得在新代码中扩大该注解的使用面。

### RC-3 维度/尺寸计算缺乏溢出与一致性防护

**机制**：`volume_size` 等以 32 位 int 计算（1a7f2）；`nsweeps×nrays×nbins×ndata_types` 乘积无溢出检查（d2238d）；解压输出尺寸不设上限（f56fc4）；`count=leng` 与缓冲区实际长度脱钩（e756f4）。

**权威依据**：CERT INT30-C/INT31-C/INT32-C；CWE-190。

### RC-4 同类组件防护强度不一致（演化分叉）

**机制**：`nexrad_level2.py:534` 对 `ngates` 做了 `min(..., max_ngates, len(data))` 钳制，而 `sband_radar.py` 同类赋值完全没有；`sigmet.py` 一个辅助函数用 `list(metadata.keys())[0]`、另一个用 `metadata.keys()[0]`；`nexrad_archive.py:327` 显式抛 `ValueError` 而 `sband_archive.py` 用 `assert`。说明同类代码由不同贡献者分叉演化，安全加固只落在部分路径上。

**权威依据**：CWE-617（可达断言）；OWASP 输入校验一致性原则。

### RC-5 安全控制可被调用参数短路

**机制**：`_validate_url` 的 `allow_private=True`（e690e4）使防护函数在 scheme 检查后直接返回 True；而默认防护本身只做主机名字面量黑名单，任何 DNS 域名均放行（d9214c）。两缺陷叠加等于 `remote.py` 的 SSRF 防护整体失效，且失效点与防护实现同文件，review 极易漏过。

**权威依据**：OWASP SSRF Prevention Cheat Sheet（应用层必须"先 DNS 解析、再按 IP 分类拒绝"，重定向后必须重新校验；黑名单/字符串匹配无效）。

### RC-6 危险 API 的不安全用法

**机制**：`os.system` 拼接字符串执行外部进程（ed8eb0）；`importlib.exec_module` 把外部文件当代码执行（e3e000）；`mkstemp` 返回值 fd 未关闭、临时文件落在进程 CWD（3fbfe4）；输出路径字符串拼接（94f00e，同时归因 RC-1）。

**权威依据**：CWE-78（命令注入）；CWE-94/95；CWE-403（文件描述符管理）；CWE-22 修复指南。

### RC-7 错误清理路径的资源管理缺陷

**机制**：`ckdtree.pyx:965-971` except 块复制粘贴错误——`if ni != NULL` 保护下 `free(mids)`，随后 `if mids != NULL` 再 `free(mids)`，第一次释放后未置 NULL（b2f856/c004f5）；`heap.remove` 未判空（a8abf7）。错误路径是测试与 review 的盲区，正常流程永远不执行。

**权威依据**：CERT MEM34-C（只释放一次）；CWE-415。

### RC-8 调试设施进入生产校验路径

**机制**：`_find_scans_to_interp` 用 `assert` 校验文件头 gate spacing（86ab7a）。`python -O`/`PYTHONOPTIMIZE=1` 下 assert 被剥离，唯一合法性检查消失；启用时又以未捕获 `AssertionError` 中断读取。同仓 `nexrad_archive.py:327` 的正确实现（显式 `ValueError`）即为天然参照。

**权威依据**：CWE-617。

### RC-9 异常冒泡导致可用性缺失（健壮性债）

**机制**：大量解析路径对畸形文件抛出未捕获的 `IndexError/ValueError/UnicodeDecodeError/struct.error/EOFError/ZeroDivisionError/TypeError`（#31、33、34、36~40 等）。单看均为低危，但它们共同决定"库处理不可信文件的失败模式"：应当是**干净拒绝（抛领域异常）而非崩溃或静默错误数据**。

### RC-10 重复发现与口径不一致（流程债）

b2f856（高危）与 c004f5（中危）为同一代码缺陷，定级不同、分析深度不同；说明扫描—验证—定级环节缺少并案与定级校准机制。修复期应合并跟踪（本计划已并案为 FU-05）。

---

## 5. 修复策略总纲

### 5.1 统一输入约束层（针对 RC-1/RC-3，最高杠杆）

新增 `pyart/io/_validate.py`（或各 reader 内共享的 `_limits` 模块），集中定义：

```
# 单文件/单场/单卷的安全上限（可按部署调整，默认取"合法最大值"）
MAX_NGATES        = 100_000      # 单射线距离门数上限
MAX_NRAYS         = 20_000       # 单扫描射线数上限
MAX_NSWEEPS       = 100          # 扫描数上限
MAX_NVOLUME_ELEMS = 2**28        # 单数组最大元素数（64M，float32 约 256MB）
MAX_FILE_BYTES    = 1 << 30      # 单文件大小上限
MAX_DECOMPRESSED  = 256 * 2**20  # bz2/gzip 流式解压输出上限（256 MiB）
MAX_DIM_PRODUCT   = 2**32        # 维度乘积上限（int 溢出防线）
```

规则：
1. 任何由文件内容推导的**分配尺寸**必须先过 `validate_dims(*dims)`：每维 > 0 且 ≤ 对应上限、乘积 ≤ `MAX_DIM_PRODUCT`、估算字节数 ≤ 当前可用内存的安全比例（可用 `psutil.virtual_memory().available * 0.5` 或固定 256MB），否则抛 `pyart.io.PyARTDataError`（新增异常类型，统一替代当前的裸 `MemoryError/IndexError/ValueError` 冒泡）。
2. 任何由文件内容推导的**长度/偏移/游标**推进必须校验 `0 <= value <= len(buffer) - already_consumed`（参照 e756f4 三类后果逐条封堵）。
3. **先校验后分配**：所有解析器在任何 `np.empty/np.ma.empty/np.ones/calloc` 之前完成 header 校验（d2238d 的教训正是"12KB 文件触发 1e19 元素分配"）。
4. 解压一律流式 + 上限：bz2 用 `BZ2Decompressor().decompress(chunk, max_length)` 或循环中累计输出，超 `MAX_DECOMPRESSED` 即中止（参照 aiohttp CVE-2025-69223 的 32MiB 上限思路取更宽松值）。

### 5.2 Cython 模块修复纪律（针对 RC-2，覆盖 9 项）

1. **契约显式化**：每个 `boundscheck(False)/wraparound(False)` 函数在入口处校验其假设（空数组、最小门数、分量个数），违例抛 `ValueError`，禁止让"参数契约允许但实现假设不成立"的取值进入循环体。
2. **注解最小化**：对解析入口函数（如 `_mask_gates_not_collected`、`unwrap_1d`）保留或恢复边界检查；性能关键的内层数值循环（如 kdp 滤波）可在入口校验后保留注解——即"外层校验、内层优化"。
3. **索引钳制**：任何由文件/用户数据派生的下标在进入禁用检查的循环前钳制到 `[0, shape-1]`（fae334 的 ray 索引、6ff3be 的 nbin、0e8f8c 的 find_min/find_max 返回值）。
4. **重新生成**：所有 `.pyx` 修改必须重新 cythonize 并提交匹配的 `.c`（仓库签入了生成的 `.c`，两处不一致本身就是缺陷传播源）。
5. **调试构建**：引入 scikit-learn 式环境开关（如 `PYART_ENABLE_DEBUG_CYTHON_DIRECTIVES=1`），在 CI 中以 `boundscheck=True/wraparound=True` 重编跑测试集，让 OOB 以 `IndexError` 形式暴露而非静默。

### 5.3 网络与文件系统边缘（针对 RC-5/RC-6）

1. `_validate_url` 重写为：scheme 白名单 → `socket.getaddrinfo` 解析全部结果 → 逐个 `ipaddress.ip_address` 分类，拒绝 loopback/private/link-local/reserved/multicast/元数据地址（含 IPv4-mapped IPv6、`::ffff:127.0.0.1`、十进制/八进制/十六进制 IP 字面量先经 `ipaddress` 归一化）→ 仅允许显式 allowlist 域名（默认空）。
2. **删除 `allow_private=True` 的 MUSIC 调用路径**；确有需要时改为调用方显式传入已校验 URL 的机制，而非旁路防护函数。
3. **重定向逐跳复检**：`_http_get` 若跟随重定向，每一跳都重新走完整校验（当前重定向间不复检）。
4. `write_grid_geotiff` 的 gdalwarp 调用改为 `subprocess.run([...], shell=False)`（优先）或 `shlex.quote(ofile)` 包裹 + 文件名字符 allowlist。
5. `plot_maxcappi`：`instrument_name`/`title` 只取 `os.path.basename` 并剥离路径分隔符，最终路径 `resolve()` 后校验包含于 `savedir` 的 `resolve()`（`os.path.commonpath` 或 `Path.relative_to`），不符则拒绝或 sanitize。
6. `load_config`：文档已声明"不要加载不可信配置"，建议 (a) 增加 `safe=False` 显式开关并默认要求调用方确认，(b) 至少对 `PYART_CONFIG` 环境变量指向 warn，(c) 长期方向是配置格式改为静态（TOML/JSON）而非 `exec_module`。
7. `radx.py`：`fd, path = tempfile.mkstemp()` 后立即 `os.close(fd)`；临时文件目录改系统临时目录（`tempfile.gettempdir()`）而非 `'.'`；用 `tempfile.mkdtemp` + `dir` + 权限控制缩小 TOCTOU 窗口，finally 中清理。

### 5.4 解析器健壮性统一模式（针对 RC-9，覆盖 12 项）

每个 reader 的畸形输入行为统一为：**捕获底层异常 → 抛 `PyARTDataError`（带文件路径与字段名）→ 不写半成品对象**。禁止向调用方冒泡裸 `IndexError/ValueError/struct.error`（这既是 DoS 面，也是下游误解为"库 bug"而非"数据畸形"的根源）。

---

## 6. 修复批次与优先级

**P0（立即，1 周内；影响 RCE/SSRF/堆破坏， Exploitability 高）**

| 波次 | 修复单元 | 内容 | 门禁 |
|---|---|---|---|
| P0-W1 | FU-01 | ed8eb0 命令注入 | 安全工程师会签 + 注入 PoC 转绿 |
| P0-W1 | FU-02 | e690e4 + d9214c SSRF | DNS-to-internal PoC 拦截 + 现有回归测试保持 |
| P0-W1 | FU-03 | 6ff3be Sigmet 负 nbins 越界写 | ASan PoC 由崩溃转为 `ValueError` |
| P0-W1 | FU-04 | 21f732 + 291237 KDP OOB 读 | ng=2/3 雷达在 ASan 下无 OOB 报告 |
| P0-W1 | FU-05 | b2f856/c004f5 cKDTree 双重释放 | 故障注入下 ASan 无 double-free、无 leak |
| P0-W1 | FU-06 | 94f00e 路径穿越 | 穿越 PoC 被拒或重定向到 savedir 内 |

**P1（3 周内；内存安全其余项 + 全部无界分配/解压炸弹）**：FU-07 ~ FU-25

**P2（6 周内；健壮性与低危项 + 流程改进）**：FU-26 ~ FU-40，并完成第 8 章模糊测试接入与第 9 章验收。

---

## 7. 逐项修复规格（42 个修复单元）

> 每项含：根因定位 → 修复动作 → 修复 Review 检查点 → 验证抓手（详见第 9 章用例表）。所有涉及 `.pyx`/`.c` 的修改必须成对提交。

### FU-01 OS 命令注入（ed8eb0，致命，CWE-78）
- **定位**：`output_to_geotiff.py:201/210`，`ofile` 两次未加引号拼入 `os.system('gdalwarp ...')`。
- **修复**：改 `subprocess.run(["gdalwarp", "-q", "-t_srs", "+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs", ofile, ofile + "_tmp.tif"], check=True)`（shell=False）。保留 shell 兼容分支时必须 `shlex.quote(ofile)` + 文件名字符 allowlist。更优解：直接用 `gdal.Warp()` Python API 免子进程。同步修正 `use_doublequotes` 文档语义。
- **Review 点**：全仓库 grep `os.system|os.popen|shell=True` 清零；测试断言注入字符串被当作字面文件名。
- **验证**：`filename='out.tif; touch /tmp/pwned; #'` → 无副作用文件产生。

### FU-02 SSRF 双缺陷（e690e4/d9214c，高危，CWE-918）
- **修复**：按 5.3 重写 `_validate_url`（scheme 白名单 + getaddrinfo 全结果 IP 分类拒绝 + allowlist 域名）；删除 `CmaMusicSource.fetch` 的 `allow_private=True`；重定向逐跳复检；`key['url']` 与 MUSIC 响应字段 URL 同等对待。
- **Review 点**：`allow_private` 参数若无合法用途则整体移除（防止重新被启用）；新增测试覆盖 `localtest.me`、`[::ffff:127.0.0.1]`、十进制 IP `2130706433`、`017700000001`、`0x7f.0.0.0x0.0x1`、`127.1`、`169.254.169.254`、RFC1918、重定向到内网 8 类必拦截；对外部域名仍应放行。
- **验证**：mock DNS 返回内网 IP → 抛拒绝异常；本地起 HTTP 服务监听 127.0.0.1 → 不被访问。

### FU-03 Sigmet 负 nbins 堆越界写（6ff3be，高危，CWE-787）
- **修复**：`_mask_gates_not_collected` 入口钳制 `nbin = min(max(nbin, 0), full_nbins)`；`_parse_ray_headers`（pyx:455）对 ray-header 第 4 列强制 `0 <= nbins <= full_nbins`（越界记为缺失射线并告警，与 `out[4] = -1` 的合法缺失路径统一）；保留或恢复该函数 boundscheck 作为纵深防御。
- **Review 点**：确认 `-1`（合法缺失）与 `< -full_nbins`（恶意）都被安全归一化；`.c` 重新生成且 `_sigmetfile.c:17400-17404` 区域无裸写。
- **验证**：ASan 下 `nbins=-32768/number_bins=100` 构造件 → 无 heap-buffer-underflow WRITE。

### FU-04 KDP 低通滤波 OOB 读（21f732/291237，高危，CWE-125）
- **修复**：`lowpass_maesaka_term` 入口 `if ng < 3: raise ValueError(...)`；`lowpass_maesaka_jac` 入口 `if ng < 4: raise ValueError(...)`；同时在公开入口 `kdp_proc.py` 的 `kdp_maesaka/kdp_vulpiani/kdp_schneebeli`（第 1410 行调用前）对 `radar.ngates` 设最小值（≥ 4，覆盖所有 finite_order 分支）。备选：恢复 boundscheck。
- **Review 点**：注意 21f732 报告指出 `ng==1` 会被 `_parse_range_resolution` 的 IndexError 先行拦截，但修复不得依赖该"偶然拦截"，仍须显式前置校验；确认 `_kdp_proc.c` 重新生成。
- **验证**：`make_empty_ppi_radar(2,...)`/`(3,...)` 构造雷达调用三个公开 KDP 函数 → `ValueError`；正常 ngates≥20 文件回归结果逐 bit 不变。

### FU-05 cKDTree 双重释放 + ni 泄漏（b2f856/c004f5，高危，CWE-415）
- **修复**：except 块改为
  ```
  except:
      if ni != <innernode*> NULL:
          stdlib.free(ni); ni = <innernode*> NULL
      if mids != <np.float64_t*> NULL:
          stdlib.free(mids); mids = <np.float64_t*> NULL
      raise
  ```
  注意释放 `ni` 前其子节点可能已被递归挂接，须确认调用方不会再认领该子树（与原代码意图一致）；`ni`/`mids` 释放后立即置 NULL。
- **Review 点**：`.c` 中两处 `free(__pyx_v_mids)`（16276/16306 附近）变为 `free(__pyx_v_ni)` + `free(__pyx_v_mids)` 各一次；并案 c004f5 关闭。
- **验证**：故障注入分配器（递归 `__build` 深处失败）→ ASan/MALLOC_CHECK_=3 无 double-free、LSan 无 leak。

### FU-06 路径穿越（94f00e，高危，CWE-22）
- **修复**：`radar_name`/`title` 分别 `os.path.basename` + 剥离 `..`/分隔符（含 URL 编码形态 `%2e%2e%2f` 防御性解码后再校验）；`figname` 解析为绝对路径后校验 `os.path.commonpath([figname, savedir]) == os.path.abspath(savedir)`，不符则替换为安全名（如 `"sanitized"`）并 warning。
- **Review 点**：`Grid.metadata`（grid_io/cfradial/nexrad_archive 读取路径）中的 `instrument_name` 全部视为不可信；绝对路径型 `instrument_name`（`/etc/cron.d/x`）也必须被 base 名化。
- **验证**：NDJSON/NetCDF 全局属性 `instrument_name='../../../../tmp/evil'` → 输出仍落在 savedir 内（或明确报错）。

### FU-07 unwrap_1d 空缓冲区越界读写（c5ca90，中危，CWE-787）
- **修复**：`_unwrap_1d.pyx` 函数入口 `if image.shape[0] == 0 or unwrapped_image.shape[0] < image.shape[0]: raise ValueError(...)`；调用方 `unwrap.py:_dealias_unwrap_1d` 对零长 ray 跳过或拒绝；考虑该解析入口恢复 boundscheck=True。
- **验证**：`unwrap_1d(np.empty(0), np.empty(0))` → `ValueError`；`dealias_unwrap_phase(radar_ngates=0, unwrap_unit='ray')` → 干净异常，ASan 无 WRITE。

### FU-08 unwrap3D calloc 未判空 + int 溢出（1a7f21，中危，CWE-476/190）
- **修复**：三个 `calloc` 返回值逐一判 NULL 并走统一清理路径（释放已分配缓冲区后返回错误码）；`volume_size` 改 `size_t` 计算并对 `width*height*depth`、`3*volume_size*sizeof(EDGE)`、`volume_size*sizeof(VOXELM)` 做 `SIZE_MAX` 前置除法检查（CERT INT30-C 形式：`a > SIZE_MAX / b` 则拒绝）；`No_of_Edges_initially` 同步改 size_t；调用方（unwrap_3d_ljmu 包装层）校验输入维度合法性。
- **Review 点**：所有 early-return 路径无泄漏（LSan 验证）；`int`→`size_t` 改动需 review 全部下游使用点。
- **验证**：`ulimit -v` 受限进程 + 接近上限的体积 → 干净错误路径；超大维度组合 → 分配前拒绝而非欠分配后越界。

### FU-09 Sigmet 压缩游程越界读（7b4e3f，中危，CWE-125）
- **修复**：`words = (compression_code + 32768) & 0x7F` 按源码注释本意掩码（或显式拒绝 `compression_code` 高位组合）；跨记录分支计算 `remain` 后校验 `remain <= len(record_buffer) - 6`（3072 元素记录缓冲）；`_get_ray` 裸指针访问加边界断言/钳制；`out` 尺寸与 `number_bins`（SINT4）一致性校验（与 `nbins` SINT2 解耦带来的独立可控性需在 header 校验层封堵）。
- **验证**：构造 `compression_code` 使 words 远超记录大小的文件 → 读取器拒绝或干净失败，ASan 无 OOB READ（数十 KB 级）。

### FU-10 NEXRAD 插值门数失配（3b55ab+89582b，中危）
- **修复（双侧）**：`_find_range_params` 改为逐矩使用 `scan_params['ngates'][i]` 计算 `last_gate`（而非 `[0]`）；`read_nexrad_archive` 在调用 `_interpolate_scan` 前校验 `4*moment_ngates <= mdata.shape[1]`（与 `scratch_ray` 分配宽度一致），不符抛 `PyARTDataError`；`nexrad_interpolate.pyx` 内核入口对 `interp_ngates`/`moment_ngates` 与传入 memoryview 宽度做契约断言；`start/end` 校验 `0 <= start <= end < nrays`。
- **Review 点**：该缺陷当前依赖"内核未启用 boundscheck"才未造成内存破坏——修复必须收敛为**显式契约拒绝**，把安全性从编译期全局设置上收回代码内；注意勿引入对合法混采样文件的误拒（需补充真实世界混合 gate-spacing 文件回归）。
- **验证**：混合门间隔构造件（250m/10 门 + 1000m/10 门）→ `PyARTDataError`；标准 NEXRAD 归档全量回归插值结果不变。

### FU-11 h_factor 越界读（4f04a8，中危，CWE-125）
- **修复**：`DistBeamRoI.__init__` 入口 `if h_factor.shape[0] != 3: raise ValueError(...)`（要求精确 3 分量，与 `gates_to_grid.py:125-126` 的扩展行为对齐）；`gates_to_grid.py:123-124,349-350` 对非 float 输入同样强制长度 3（拒绝而非透传任意长度元组/列表）。
- **验证**：`map_gates_to_grid(..., h_factor=(1.0, 1.0))` 和 `()` → `ValueError`；默认与显式 `(1,1,1)` 结果不变。

### FU-12 k=0 查询堆下溢/越界写（a8abf7，中危）
- **修复**：`cKDTree.query` 公开层 `if k < 1: raise ValueError(...)`；`heap.remove()` 入口 `if self.n == 0: return/raise`；`heap(k)` 构造处校验 `k>=1`。三重防线（公开 API、容器构造、数据结构操作）。
- **验证**：`cKDTree(data).query(x, k=0)` → `ValueError`；`k=1` 正常路径结果不变（gate_mapper 现有调用方回归）。

### FU-13 MDV RLE8 越界读（d7c300，中危，CWE-125/131）
- **修复**：解码循环改为 `while data_ptr + 2 < len(data)` 式边界前置（或每次前进后校验）；输出缓冲按实际写入量动态扩展或按头部 `nbytes_coded` 与实际解码量取最小值；所有索引访问前 `0 <= idx < len` 校验。越界/截断统一抛 `PyARTDataError`。
- **验证**：末字节恰为转义 key 的 compr_data → 无 IndexError 冒泡、无 OOB；正常 MDV 文件解码结果逐字节一致。

### FU-14 Sigmet 无界分配（d2238d，中危，CWE-789）
- **修复**：`read_data` 在 `np.ma.empty(shape)` 前执行 `validate_dims(nsweeps, nrays, nbins)`（正值 + 单维上限 + 乘积上限 + 类型数上限）；`data_type_names` 数量钳制（≤ 合法类型集合，而非 128 个任意置位）；`_get_sweep:281` 的 `np.ones((nrays, nbins+6))` 同样先校验。
- **验证**：`nsweeps=32767, nrays=65535, nbins=2^31-1` 构造头（12KB 文件）→ 读取前即被 `PyARTDataError` 拒绝，进程 RSS 无峰值。

### FU-15 MDV 字段头无界分配（b94672，中危，CWE-789）
- **修复**：`read_a_field` 校验 `0 < nx,ny,nz <= MAX`、与 master header 的 `max_nx/max_ny/max_nz` 比对、与文件实际剩余字节数比对（`nx*ny*nz*sizeof <= file_remaining`）；`_get_levels_info` 的 `struct` 解析先确认 `len(buf) >= calcsize(fmt)`。
- **验证**：`nx=ny=nz=2^31-1` 构造件 → 拒绝；正常字段解码不变。

### FU-16 Level III 径向/门数（18f97e，中危）
- **修复**：`_read_symbology_block` 对 `nradials/nbins` 过 `validate_dims`；packet_code==16 分支校验 `pos + nbins <= len(buf)`（不足则拒绝而非静默截断——静默截断导致的形状不符/未初始化距离门都是数据污染）；RLE 分支 `nbytes*2` 与实际缓冲长度校验。
- **验证**：畸形 nradials/nbins 组合与截断型 packet → `PyARTDataError`；合法 Level III 产品全量回归。

### FU-17 Level III packet-28（8227a7，中危）
- **修复**：`_read_symbology_block_28` 先判空 radials 再取 `radials[0]`；`num_bytes` 切片前校验 `pos+num_bytes <= len(buf)`；`nradials/nbins` 过 `validate_dims`；XDR 解析输入先做长度前置断言，`EOFError/ValueError` 包装为 `PyARTDataError`。
- **验证**：空 components 的 176 号产品构造件 → 干净拒绝；合法文件不变。

### FU-18 NEXRAD bz2 解压炸弹（f56fc4，中危，CWE-409/400）
- **修复**：流式 `BZ2Decompressor` + 累计输出上限 `MAX_DECOMPRESSED`（超限抛 `PyARTDataError`）；限制单记录解压输出 ≤ 记录声明大小的合理倍数；解压后总缓冲大小上限。参照 aiohttp CVE-2025-69223（32MiB 上限）与 CPython CVE-2026-15310（bzip2 预分配上限）思路。
- **验证**：1 KB 构造 bz2（理论展开数 GB）→ 在 256MiB 处中止；真实 L2 文件解码结果一致。

### FU-19 C98D 有符号长度（e756f4，中危）
- **修复**：`leng` 先判 `0 < leng <= len(buf) - self.pos` 再用于切片/游标；`np.frombuffer` 的 `count` 必须与实际切片长度一致（杜绝 `count=-1` "读到末尾"语义）；`self.pos += leng` 仅在校验通过后执行；外层循环加最大迭代/钳制，防游标回退导致的错位解析循环；解析失败统一 `PyARTDataError`。
- **验证**：`leng=-1` 与 `leng>` 剩余 两类构造件 → 拒绝；正常 C98D 文件不变。

### FU-20 CF/Radial 可变门（7a67cb，中危）
- **修复**：`ray_n_gates`/`ray_start_index` 使用前校验：`idx+gates <= flat_field_len`、`gates <= range_dim`、条目数 ≤ time 维长度；冲突时抛 `PyARTDataError`（不半途写坏 Radar）。
- **验证**：越界 ray_n_gates 构造件 → 拒绝；合法可变门文件不变。

### FU-21 rxm25 无界分配（434d5a，中危，CWE-789）
- **修复**：`read_rxm25` 对 NetCDF `Gate/Radial` 维度过 `validate_dims` 并与变量实际存储规模（文件大小、变量 dtype 与 nc 属性）交叉校验；负数/超大维度直接拒绝。
- **验证**：伪造 `NumGates=1e10`、`Radial=1e10` 文件 → 读取前拒绝；正常 RXM-25 文件不变。

### FU-22 rxm25 变量 shape 一致性（17bab9，中危）
- **修复**：13 个字段变量写入前统一校验 `shape == (Radial, Gate)`（或显式声明的兼容 shape），不符抛 `PyARTDataError`；`radar.add_field` 前校验，保证 Radar 对象内部自洽（下游 Cython 内核按 (ray,gate) 索引）。
- **验证**：Velocity shape 与 Radial/Gate 维不一致的构造件 → 拒绝；正常文件不变。

### FU-23 GAMIC 'sets' 无界分配（57a9dd，中危，CWE-789）
- **修复**：`GAMICFile.__init__`：`sets` 钳制到 `MAX_NSWEEPS` 且 > 0；先用文件组列表长度与实际扫描数交叉验证（`len(f['what'].keys())` 之类），不一致拒绝；`how_attrs()/what_attrs()` 循环上限随之受控。
- **验证**：`sets=1e9` HDF5 → 拒绝；正常 GAMIC 文件不变。

### FU-24 d3r NumGates 无界分配（db0886，中危，CWE-789）
- **修复**：`NumGates` 过 `validate_dims`；所有 `ncvar[:]` 物化前估算总字节并与文件大小交叉校验；`Elevation/Azimuth/Time` 与字段维度一致性校验。
- **验证**：`NumGates=1e10` 构造件 → 拒绝；正常文件不变。

### FU-25 sband_radar ngates 钳制（9b7666，中危）
- **修复**：对齐 `nexrad_level2.py:534` 的成熟写法：`ngates = min(msg[moment]['ngates'], max_ngates, len(msg[moment]['data']))`；`max_ngates` 按全卷射线计算（max 取保守上界而非首条射线）。
- **验证**：后续消息声明门数 > max_ngates 的构造件 → 钳制后正常完成或干净拒绝；合法 S 波段文件全量回归。

### FU-26 MSG31 名称与指针（c73c45，中危）
- **修复**：名称三字节解码用 `errors='replace'` 或显式 ASCII 校验后转安全标识；`ptr`/`ptr2` 与缓冲区剩余长度校验后再 `_unpack_from_buf`；`ptr2+ngates*2 > len(buf)` 时拒绝而非让 `np.frombuffer` 静默截断。
- **验证**：非 ASCII 名称字节（0x80-0xFF）与越界指针构造件 → `PyARTDataError`；正常文件不变。

### FU-27 MSG31 size/block_pointer（f89e9e，中危）
- **修复**：`msg_size` 下界校验（≥ 头部最小尺寸）且 `pos+msg_size <= len(mbuf)`；`block_pointer`（≤65535）先校验 `<= len(record)`；解析循环中 MSG_31 的异常就地转换为 `PyARTDataError`（当前仅消息类型 5 有捕获）。
- **验证**：size=0/1、越界 block_pointer 构造件 → 拒绝；正常文件不变。

### FU-28 S 波段 assert 校验（86ab7a，中危，CWE-617）
- **修复**：`_find_scans_to_interp` 将 assert 改为与 `nexrad_archive.py:327` 一致的显式 `ValueError("Gate spacing is neither 1/4 or 1/2")`，浮点比较用容差；顺带将 `moment_ngates` 校验委托 FU-10 的统一契约。
- **验证**：`python -O` 下畸形门间距文件 → 仍被显式拒绝（这是本项核心验收：-O 模式测试）；非 -O 下也抛 `ValueError` 而非 `AssertionError`。

### FU-29 unwrap2D int 截断（854841，低危，CWE-681）
- **修复**：调用前 `if max(shape) > INT_MAX: raise OverflowError`；或把原型与实现改 `Py_ssize_t/long`（推荐，顺带消除 32 位构建面）。`_unwrap_2d` 的调用点补维度校验。
- **验证**：32 位构建（或模拟）下 `shape=2^30` 数组 → 显式拒绝；正常数据回归。

### FU-30 find_window 负射线索引（fae334，低危，CWE-125）
- **修复**：`find_window` 对调整后 startray/endray 做 `[0, numRays-1]` 钳制（与 window() 对 firstbin/lastbin 的处理对齐）；`proximity` 入参校验 `>= 0`；调用方（dealias 参数）校验 proximity 合理范围。
- **验证**：极大 proximity + 小 numRays 构造 → 无负下标解引用，ASan 干净。

### FU-31 X-band 未初始化场（bf4710，低危，CWE-908）
- **修复**：`_read_volume` 改 `np.zeros`（或对未匹配帧明确填 NaN 掩码值并在 metadata 记录）；填充后校验"每行至少被写过一次"，未写行显式置 NaN；`nrays/ngates` 非零校验（顺带修 `_radar_from_arrays` 的 `ranges[1]` IndexError）。
- **验证**：`data_type` 与 code 不匹配的构造件 → 字段为 NaN 而非堆残留字节；字段值可复现（无地址依赖）。

### FU-32 find_min/find_max 转换（0e8f8c，低危，CWE-681/UB）
- **修复**：`int` 转换前做 `if not (0 <= val <= na-1): raise/clamp`；`step == 0` 显式报错（不再静默返回 0 掩盖配置错误）；用 `lround`+范围检查替代裸 cast。
- **验证**：极小 grid_steps 构造 → 无 UB 下 clamp 行为由 ASan/UBSan（`-fsanitize=float-cast-overflow`）确认。

### FU-33 sigmet dict_keys（268283，低危，健壮性）
- **修复**：`metadata.keys()[0]` → `list(metadata.keys())[0]`（与同函数族 L512 对齐），并加空 dict 分支判断。
- **验证**：无扩展头 Sigmet 文件请求顺序时间排序 → 干净异常而非 TypeError 中断语义混乱。

### FU-34 read_sigmet 除零（5f2e4b，低危）
- **修复**：`nbins == 0` 显式抛 `PyARTDataError`；门间隔计算与 reshape 前统一 `validate_dims`（与 FU-14/16 同一约束层）。
- **验证**：`number_bins=0` IRIS 文件 → 拒绝；正常文件不变。

### FU-35 UF 有符号记录长度（107f50，低危）
- **修复**：`record_size < header_len` 或负值 → 拒绝；`fobj.read()` 读长上限 `MAX_FILE_BYTES`；UFRay 头偏移与剩余长度校验。
- **验证**：负 record_length 文件 → 拒绝而非整文件缓冲；正常 UF 文件不变。

### FU-36 CHL 有符号块长（376b4c，低危）
- **修复**：块头解析校验 `length >= 8` 且 `length <= remaining`；`while packet is not None` 循环加游标前进保证与最大迭代上界，杜绝"读整个文件当下一个块头"的错位。
- **验证**：`length=0/负值` 构造件 → 拒绝；正常 CHL 文件不变。

### FU-37 KAZR 索引（23c53c，低危）
- **修复**：`re.findall(...)[0]` → 先判空再取；`float()` 转换包 `ValueError` → `PyARTDataError`。
- **验证**：`radar_operating_frequency=""`/`"GHz"` 构造件 → 拒绝；正常文件不变。

### FU-38 SINARAME 组名（7dc6bf，低危）
- **修复**：组名解析用正则显式匹配 `^dataset(\d+)$` 过滤（非匹配组跳过并 warning）；`datasets[0]` 前判空。
- **验证**：`dataset_meta`/`datasetXYZ` 顶层组 → 被忽略而非 ValueError；正常文件不变。

### FU-39 配置即代码（e3e000，低危，CWE-94/95）
- **修复**：`load_config` 增加显式 `trusted=False` 参数，非可信路径调用时 warning 或要求确认；`PYART_CONFIG` 指向非常规路径时启动 warning；文档强化"配置=代码"声明；中期迁移静态配置格式评估。
- **Review 点**：这是**设计层决策**而非单纯 bug（回归测试断言现有行为不破坏），须 upstream 设计讨论后实施。
- **验证**：默认调用路径行为不变；恶意配置文件仅在显式 trusted=True 时加载（契约测试）。

### FU-40 fd 泄漏/TOCTOU（3fbfe4，低危）
- **修复**：`fd, path = tempfile.mkstemp()` → 立即 `os.close(fd)`；目录改 `tempfile.gettempdir()`；mkstemp→外部 RadxConvert 写入→`os.remove` 之间用 `O_EXCL` 语义/专用子目录收窄 TOCTOU 窗口；`finally` 幂等清理。
- **验证**：循环 read_radx 10000 次 fd 数不增长（`/proc/self/fd` 计数断言）；符号链接替换场景下行为定义明确。

---

## 8. 修复 Review 流程

### 8.1 提交纪律（每修复单元必须同时满足）

1. **代码**：最小化 diff，不夹带无关重构；C 示例函数处注释前置条件（`@param h_factor must have exactly 3 elements`）。
2. **回归测试**：至少 1 个恶意输入用例 + 1 个合法输入用例（证明修复不破坏正常功能），命名 `test_io_xxx_rejects_malformed_*` / `test_io_xxx_accepts_*`。
3. **PoC/样本**：新增构造样本入 `tests/data/malicious/`（按格式分目录：`sigmet/mdv/nexrad_level2/nexrad_level3/uf/chl/c98d/cfradial/gamic/rxm25/d3r/cinrad/sinarame/kazr`），样本生成脚本入库（保证可重建、可审计）。
4. **`.c` 同步**：凡改 `.pyx`，提交重新生成的 `.c`，CI 校验"pyx 与 c 的时间戳/内容一致性"。
5. **性能声明**：对 `pyart/correct`、`pyart/retrieve`、`pyart/map` 数值路径，附同一 workload 前后耗时（不得超过 +2%）。

### 8.2 Review 检查单（Reviewer 逐项打勾）

**通用（所有 FU）**
- [ ] 输入校验发生在**任何分配/索引/指针运算之前**
- [ ] 校验覆盖边界值：0、1、-1、INT_MIN/INT_MAX、SIZE_MAX/2、恰好等于上限、恰好越上限一
- [ ] 失败模式为受控异常（`PyARTDataError`），非裸异常冒泡、非段错误、非静默错误数据
- [ ] 无新增 `assert` 用于不可信输入校验
- [ ] 错误/清理路径无泄漏、无 double free（对照 CERT MEM30-C/MEM34-C）
- [ ] 测试包含攻击向量与良性对照，样本入库

**内存安全类（FU-03/04/05/07/08/09/11/12/29/30/32）**
- [ ] 禁用 boundscheck/wraparound 的函数，其参数契约在入口处被显式穷举校验
- [ ] 验证过修复在 `boundscheck=True` 调试构建下同样通过（行为一致）
- [ ] `.pyx` 与 `.c` 同步重新生成

**解析器类（FU-14~28、33~38）**
- [ ] 所有文件头维度过统一 `validate_dims`（正值/上限/乘积/与文件大小一致性）
- [ ] 所有长度/偏移/游标校验 `0 <= v <= remaining`
- [ ] 解压路径有输出上限（FU-18 及同类）
- [ ] 同类读取器实现一致性对照（如 sband vs nexrad_level2）

**网络/文件系统类（FU-01/02/06/39/40）**
- [ ] 无 `os.system`/`shell=True`/未转义拼接
- [ ] SSRF 校验覆盖 DNS 解析后 IP 分类 + 重定向逐跳
- [ ] 输出路径 `resolve()` 后做目录包含性校验
- [ ] 临时文件在系统临时目录、fd 正确关闭、TOCTOU 窗口评估

### 8.3 会签制度

- P0 六个修复单元：模块 owner + 安全工程师双人会签，附完整 PoC 输出截图/日志。
- Cython `.c` 重新生成由 CI 校验，不允许手工编辑生成文件。
- 每波次完成后召开 30 分钟修复评审会，逐单元过 8.2 检查单并归档结论。

---

## 9. 测试计划

### 9.1 测试分层

| 层 | 手段 | 目标 | 承担阶段 |
|---|---|---|---|
| L1 单元/回归 | pytest + 新增畸形/良性用例 | 功能正确性、失败模式正确 | 每 FU 提交时 |
| L2 内存安全 | ASan+UBSan+LSan 构建全量测试 | 越界、UB、泄漏、双重释放 | P0/P1 每 FU；P2 抽检 |
| L3 模式矩阵 | `python -O` vs 非 -O；32/64 位；numpy 1.x/2.x | 断言剥离、截断宽度、ABI 兼容 | P1 起每波次 |
| L4 性能基准 | 固定 workload 计时 | 修复不引入 >2% 回退 | P1 起每波次 |
| L5 模糊测试 | Atheris harness 覆盖各 parser | 发现清单外同类缺陷 | P2 起持续 |
| L6 端到端 PoC | 恶意样本全链路读取 | 攻击链整体失效 | 每波次验收 |

### 9.2 测试环境矩阵

| 维度 | 取值 |
|---|---|
| 构建 | `setup.py build_ext` 默认；`CFLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer"` sanitizer 构建；`PYART_ENABLE_DEBUG_CYTHON_DIRECTIVES=1` 调试构建 |
| 优化级 | `python` 与 `python -O`（PYTHONOPTIMIZE=1）双跑 |
| 位宽 | linux-x86_64 为主；关键 C 模块（FU-08/29/30/32）补 linux-i386 交叉验证 |
| numpy | 1.26 与 2.x 双版本（Py-ART 已知 NumPy 2 ABI 敏感） |
| 内存限制 | `ulimit -v` / cgroup memory limit 用于 FU-14/15/16/21/23/24 的"拒绝而非 OOM"验证 |

### 9.3 逐修复单元测试用例

> 检测手段：A=ASan/UBSan/LSan，G=gdb/faulthandler，M=内存 RSS 监控，R=结果字节级比对，E=异常类型断言。

| 用例ID | 修复单元 | 测试输入 | 期望行为 | 手段 |
|---|---|---|---|---|
| TC-01a | FU-01 | `write_grid_geotiff(grid, 'out.tif; touch /tmp/pwned; #', 'reflectivity', warp=True)` | 无 `/tmp/pwned`；gdalwarp 以字面文件名执行/或 API 内建 warp | E |
| TC-01b | FU-01 | `use_doublequotes=True/False` 两分支 + 正常文件名 | 正常导出成功 | R |
| TC-02a | FU-02 | `_validate_url('http://localtest.me/')`（DNS→127.0.0.1） | 拒绝 | E |
| TC-02b | FU-02 | `http://[::ffff:127.0.0.1]/`、`http://2130706433/`、`http://017700000001/`、`http://0x7f.0x0.0x0.0x1/`、`http://127.1/` | 全部拒绝 | E |
| TC-02c | FU-02 | `http://169.254.169.254/latest/meta-data/`、RFC1918 三址段、IPv6 ULA | 全部拒绝 | E |
| TC-02d | FU-02 | 公开 HTTPS 科研数据 URL | 放行（无回归） | E |
| TC-02e | FU-02 | `CmaMusicSource.fetch(key={'url': 'http://127.0.0.1:6379/'})` | 拒绝，不发起连接 | E |
| TC-02f | FU-02 | 上游响应 mock 重定向到内网 | 逐跳拒绝 | E |
| TC-03a | FU-03 | Sigmet 文件：ray-header word4=0x8000(-32768)，PRODUCT_END number_bins=100 | ASan 无 WRITE 越界；行为为安全归一化/缺失射线告警 | A |
| TC-03b | FU-03 | ray nbins=-1（合法缺失） | 按缺失射线处理，结果不变 | R |
| TC-04a | FU-04 | `kdp_maesaka(radar_ngates=2)` / `kdp_vulpiani` / `kdp_schneebeli` | `ValueError`，ASan 干净 | A+E |
| TC-04b | FU-04 | 标准测试雷达（ngates≥1000） | 结果与修复前逐 bit 一致 | R |
| TC-05a | FU-05 | 故障注入：递归 `__build` 深处 malloc 失败 | ASan 无 double-free；LSan 无泄漏 | A |
| TC-05b | FU-05 | `NNLocator(big_array, algorithm='kd_tree')` 正常构建 | 结果不变 | R |
| TC-06a | FU-06 | `instrument_name='../../../../tmp/evil'` 的网格元数据 + `plot_maxcappi(savedir=tmpdir)` | 输出文件落在 tmpdir 内（或拒绝） | E |
| TC-06b | FU-06 | `instrument_name='/etc/cron.d/x'`、`title='../evil'`、`'%2e%2e%2fetc%2fx'` | 均不逃逸 savedir | E |
| TC-07a | FU-07 | `unwrap_1d(np.empty(0,dtype=f8), np.empty(0,dtype=f8))` | `ValueError`，ASan 无 WRITE | A+E |
| TC-07b | FU-07 | `dealias_unwrap_phase(radar_ngates=0, unwrap_unit='ray')` | 受控异常 | E |
| TC-08a | FU-08 | `ulimit -v` 下触发 `mids/voxel/edge` calloc 失败 | 错误码路径，LSan 无泄漏 | A |
| TC-08b | FU-08 | width*height*depth 使 32 位 int 溢出的体积 | 分配前拒绝 | E |
| TC-09a | FU-09 | compression_code 使 words>记录容量的 Sigmet 文件 | ASan 无 OOB READ | A |
| TC-10a | FU-10 | 同扫描混合门间隔（250m/10门+1000m/10门）NEXRAD 归档 | `PyARTDataError`（拒绝） | E |
| TC-10b | FU-10 | `_fast_interpolate_scan_2/4` 合法数据 | 插值结果不变 | R |
| TC-11a | FU-11 | `map_gates_to_grid(..., h_factor=(1.0,1.0))`、`h_factor=()` | `ValueError` | E |
| TC-11b | FU-11 | 默认 h_factor 与 `(1.,1.,1.)` | 网格结果不变 | R |
| TC-12a | FU-12 | `cKDTree(data).query(x, k=0)` | `ValueError` | E |
| TC-12b | FU-12 | `query(x, k=1)`（gate_mapper 现有路径） | 结果不变 | R |
| TC-13a | FU-13 | compr_data 末字节=转义 key 的 MDV | 无 IndexError 冒泡、无 OOB | A |
| TC-14a | FU-14 | 12KB Sigmet：nsweeps=32767,nrays=65535,nbins=2^31-1 | 读取前拒绝，RSS 峰值 < 100MB | M+E |
| TC-15a | FU-15 | MDV 字段头 nx=ny=nz=2^31-1 | 拒绝；与 master header 不符也拒绝 | E |
| TC-16a | FU-16 | Level III：nradials/nbins 巨型值；truncated packet_code=16 | 拒绝；无静默截断导致的未初始化距离门 | E+R |
| TC-17a | FU-17 | Level III #176，XDR components 为空列表 | 干净拒绝 | E |
| TC-18a | FU-18 | 1KB bz2 记录（理论展开 GB 级） | 在 256MiB 上限处中止 | M+E |
| TC-18b | FU-18 | 真实 NEXRAD L2 bz2 文件 | 解码结果不变 | R |
| TC-19a | FU-19 | C98D：leng=-1；leng>剩余字节；负 leng 致游标回退 | 全部拒绝 | E |
| TC-20a | FU-20 | CF/Radial：ray_n_gates 越 time 维/range 维/扁平域 | 拒绝 | E |
| TC-21a | FU-21 | RXM-25：Gate=Radial=1e10 | 读取前拒绝 | M+E |
| TC-22a | FU-22 | RXM-25：Velocity shape≠(Radial,Gate) | 拒绝 | E |
| TC-23a | FU-23 | GAMIC HDF5：sets=1e9 | 拒绝 | M+E |
| TC-24a | FU-24 | d3r nc：NumGates=1e10 | 拒绝 | M+E |
| TC-25a | FU-25 | S 波段：第二条消息 ngates>max_ngates | 钳制完成或干净拒绝 | E |
| TC-26a | FU-26 | MSG31：名称字节 0x80-0xFF；ptr>len(buf) | 拒绝 | E |
| TC-27a | FU-27 | MSG31：size=0/1；block_pointer=65535>记录长 | 拒绝 | E |
| TC-28a | FU-28 | 门间距=1/3 构造文件，`python -O` 运行 | 仍被拒绝（核心）；非 -O 抛 ValueError 非 AssertionError | E |
| TC-29a | FU-29 | unwrap2D shape 分量>INT_MAX（i386 构建） | OverflowError/拒绝 | E |
| TC-30a | FU-30 | dealign：proximity≫numRays 的扫描参数 | 无负下标解引用，结果定义明确 | A |
| TC-31a | FU-31 | X 波段：帧 data_type 与 code 全不匹配 | 字段全 NaN 可复现，无堆残留 | R |
| TC-32a | FU-32 | grid_steps=1e-300；step=0 | 无 UB（UBSan float-cast-overflow）；step=0 显式报错 | A+E |
| TC-33a | FU-33 | 无扩展头 Sigmet + 顺序时间排序请求 | 干净异常（非 TypeError 语义混乱） | E |
| TC-34a | FU-34 | IRIS：number_bins=0 | 拒绝（无 ZeroDivisionError） | E |
| TC-35a | FU-35 | UF：record_length 为负 | 拒绝；无整文件缓冲 | M+E |
| TC-36a | FU-36 | CHL：length=0/-1 | 拒绝；无游标错乱 | E |
| TC-37a | FU-37 | KAZR：radar_operating_frequency="" / "GHz" | 拒绝 | E |
| TC-38a | FU-38 | SINARAME：顶层组 "dataset_meta"/"datasetXYZ" | 被忽略，读取继续 | E |
| TC-39a | FU-39 | 恶意内容配置文件经默认 load_config 调用 | 行为定义明确（warning/显式 trusted 开关） | E |
| TC-40a | FU-40 | read_radx ×10000 循环 | fd 计数不增长；finally 清理幂等 | G |

### 9.4 恶意样本库建设

- **结构**：`tests/data/malicious/<format>/<scenario>.<ext>` + `tests/data/malicious/generate.py`（每个样本附带生成参数与对应 TC 编号注释）。
- **覆盖矩阵**：每个格式至少覆盖 (a) 巨型维度头、(b) 零/负长度字段、(c) 游标越界、(d) 类型/编码异常值、(e) 空容器五类。
- **基线快照**：每份合法测试文件同时生成"bit 级输出快照"，防止修复引入功能性回归（对 `pyart/io` 24 项全覆盖）。

### 9.5 模糊测试接入（P2 起）

1. **Harness**：为每个 reader 写 Atheris harness（`tests/fuzz/fuzz_<format>.py`），entry 为 `pyart.io.read_*` / `pyart.io.read`（自动识别路径），输入为原始字节 → 写入临时文件 → 调用读取器；所有可预期异常（新 `PyARTDataError` 及其包装）在 harness 内吞掉，仅让崩溃/ASan 报告暴露。
2. **种子语料**：现有 `tests/data/` 全部合法文件 + 本计划恶意样本。
3. **CI 冒烟**：每 harness 每次 PR 跑 5 分钟（`-max_total_time=300`），崩溃即失败；隔夜跑长时。
4. **OSS-Fuzz 集成**（后续）：将 arm-pyart 接入 OSS-Fuzz（Python 语言、Atheris 引擎），把 pyart.io 纳入持续模糊测试。

### 9.6 性能门槛与 CI 门禁

- 门禁 1：L1~L3 全绿（含 `python -O` 双跑）。
- 门禁 2：`pyart/correct`、`pyart/retrieve`、`pyart/map` 数值路径性能回退 ≤2%。
- 门禁 3：每修复单元的恶意样本回归红→绿→保持不变（防止后续重构再引入）。
- 门禁 4：`.pyx`/`.c` 一致性校验（CI 重新 cythonize 后 diff 为空）。

---

## 10. 验收标准（Definition of Done）

全部满足方可关闭本轮：

1. 44 条报告逐条核销：修复（或按 upstream 决策明确 WONTFIX 并记录理由——目前看仅 FU-39 涉及设计讨论）、关联 TC 转绿、样本入库、`PyARTDataError` 语义统一落地。
2. L1~L3 全绿：含 ASan/UBSan/LSan 全量测试、`python -O` 双跑、64/32 位关键模块交叉验证。
3. P0 六单元全部经安全工程师会签，PoC 从"崩溃/注入/越权"转为"受控拒绝"。
4. `pyart.io` 与 `pyart/aux_io` 全部 public 入口对畸形输入不再产生段错误、堆破坏、未捕获裸异常冒泡。
5. 模糊测试 CI 连续运行 7 天无新增崩溃类发现（或有发现则已闭环）。
6. 性能门槛达标，无科学计算功能回归（bit 级快照比对）。
7. 上游协作：按 ARM-DOE/pyart issue tracker 流程提交修复 PR（越界写/双重释放/命令注入类敏感问题应先私密报告维护者，协商 CVE 披露节奏；注意 PyPI/Snyk 当前显示无已知 CVE，属首次披露，需预留 embargo 期）。

---

## 11. 残余风险与后续 hardening 方向

1. **未知同类模式**：本报告为单轮扫描结果；RC-1 模式（信任边界缺失）高度可疑存在未扫描到的同类（如 radar_io 中未覆盖的 legacy 路径、`xradar` 访问器）。模糊测试与"Atheris 全入口覆盖"是发现残余的主要手段。
2. **性能与安全的长期张力**：`boundscheck(False)` 全面回退会伤害 ARM 数据吞吐场景。建议 upstream 采纳"外层校验+内层注解"的项目级规范（写入 CONTRIBUTING），并评估只对解析入口函数重编带检查构建。
3. **错误类型设计**：引入 `PyARTDataError` 是行为变更，需 major/minor 版本说明与迁移指南（当前调用方可能依赖裸异常类型做流控）。
4. **依赖链**：`arm_pyart` 依赖 numpy/gdal/netCDF4/h5py 等，本计划覆盖自身代码；建议将依赖纳入 SCA 持续监控（Snyk/OSV）。
5. **披露策略**：ed8eb0/e690e4/6ff3be 具有 RCE/SSRF/堆写性质，建议 upstream 协调披露（CNA 请求或 GitHub Security Advisory），修复分支先行、版本发布后公开细节。

---

## 12. 参考资料

1. monkeyscan 报告：CSV 汇总表 + 44 份缺陷详报（本计划输入）。
2. Cython 官方文档 — Source Files and Compilation（boundscheck/wraparound 指令语义）：https://docs.cython.org/en/latest/src/userguide/source_files_and_compilation.html
3. scikit-learn — Cython Best Practices（调试期恢复 boundscheck 的环境变量模式）：https://scikit-learn.org/stable/developers/cython.html
4. OWASP — Server-Side Request Forgery Prevention Cheat Sheet（DNS 解析后 IP 分类、重定向复检）：https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet
5. SEI CERT C Coding Standard（INT04-C/INT30-C/INT31-C/INT32-C/MEM30-C/MEM34-C/EXP34-C）：https://wiki.sei.cmu.edu/confluence/display/c/SEI+CERT+C+Coding+Standard
6. MITRE CWE：CWE-22/78/94/125/190/400/409/415/476/617/681/787/789/908/918/403/377。
7. CWE-409 修复模式（解压输出上限）：aiohttp CVE-2025-69223（32 MiB 上限）、CPython CVE-2026-15310（bzip2/LZMA 预分配上限）。
8. CWE-22 修复模式（canonicalize + containment）：Python `os.path.realpath` + `commonpath` 校验。
9. Pillow 12.3.0 系列 CVE（CWE-125/190/400/789/835）——科学计算库头部字段失控的同类行业判例。
10. Google OSS-Fuzz / Atheris（Python 覆盖引导模糊测试）：https://google.github.io/oss-fuzz/
