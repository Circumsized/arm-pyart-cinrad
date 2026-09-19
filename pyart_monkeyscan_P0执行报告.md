# Py-ART CINRAD fork Monkeyscan 漏洞修复 —— P0 波次执行报告

- 依据：《pyart_monkeyscan_修复Review测试计划.md》v2 第 6 章（P0 波次）与第 7 章（逐项修复规格）
- 工作副本：`/workspace/pyart`（即被扫描 fork：Circumsized/arm-pyart-cinrad @ 40b0937，editable install）
- 方法：TDD（每项先写失败测试观测 RED，再修复至 GREEN）；Cython 修改遵循 .pyx → .c → .so 重建链
- 环境：Python 3.14.7 / numpy 2.5.3 / gcc 13.3 / pytest 9.1.1

## 1. P0 变更清单（6 项 FU，覆盖 8 个缺陷 ID）

| FU | 缺陷 ID | CWE / CERT | 文件 | 修复要点 |
|---|---|---|---|---|
| FU-01 | ed8eb0 | 78 / — | `pyart/io/output_to_geotiff.py` | 两处 `os.system('gdalwarp …')` 改为 `subprocess.run([...], check=True)`；顺带修复 `_create_sld` 第二个 shell 汇点（`filename.split(".")` → `os.path.splitext`） |
| FU-02 | e690e4, d9214c | 918 / — | `pyart/io/remote.py` | `_validate_url` 重写：scheme 校验 → allowlist → `getaddrinfo` 全地址解析 → `ipaddress` 分类（含 ipv4-mapped/sixtofour 解包、额外阻断 0.0.0.0/8、100.64/10、192.0.0.0/24、198.18/15、64:ff9b::/96）；MUSIC 拉取移除 `allow_private=True` 旁路 |
| FU-03 | 6ff3be | 787 / INT04-C | `pyart/io/_sigmetfile.pyx`(+.c+.so) | `_mask_gates_not_collected` 对不可信 `nbin` 钳位到 `[0, full_nbins]` |
| FU-04 | 21f732, 291237 | 125 / — | `pyart/retrieve/_kdp_proc.pyx`(+.c+.so)、`pyart/retrieve/kdp_proc.py` | `lowpass_maesaka_term` 增加 `ng < 3` 守卫、`lowpass_maesaka_jac` 增加 `ng < 4` 守卫；`kdp_maesaka` 入口增加 `radar.ngates < 4` 守卫 |
| FU-05 | b2f856, c004f5 | 415 / MEM31-C | `pyart/map/ckdtree.pyx`(+.c+.so) | `__build` except 块：原代码在 `ni != NULL` 时 `free(mids)`（误释放 + 泄漏 ni）随后再次 `free(mids)`（双重释放）；改为各释放一次并置 NULL |
| FU-06 | 94f00e | 22 / — | `pyart/graph/max_cappi.py` | 新增 `_safe_filename_component()` 规范化 `title`/`radar_name`，输出路径做 `realpath` 包含性校验，越界时回退安全文件名并告警 |

## 2. TDD RED/GREEN 证据

### FU-05（cKDTree 双重释放）—— 本轮焦点
- RED：LD_PRELOAD malloc 故障注入（`pyart/map/tests/fu05_fail_malloc_interposer.c`，解释器启动后的 magic-size malloc 布武，仅干扰树构建窗口的分配；live/freed 指针哈希表做确定性双重释放检测，零误报）。修复前扫描 k=1..40：**39/40 次确诊 double free**（`FU05-DOUBLE-FREE-DETECTED`，退出码 42）；k=1 为根节点 mids 自身失败（free(NULL) 无害）故干净。pytest RED：8 failed / 1 passed。
- GREEN：`.pyx` 修复 → cython 重生 `.c` → 重编 `.so` 后：扫描 k=1..40 **0/40 双重释放，40/40 干净 MemoryError 传播**；pytest GREEN：**9/9 passed**（含 harness 自校验：注入窗口覆盖构建，target 分配数 5770 ≥ 5000）。
- 证据存档：`/tmp/fu05_red_before.txt`、`/tmp/fu05_green_after.txt`。
- 说明：故障注入器以解释器/numpy 启动后的 magic-size malloc 布武，故不扰动 CPython 启动分配；detector 对 realloc/reallocarray 簿记保持幂等（glibc reallocarray 内部回调公共 realloc 符号会造成双重簿记）。

### FU-01（os.system 命令注入）
- RED：假 GDAL + 记录型 subprocess/os.system sink，断言 `os.system` 未被调用 → 失败（存在 `gdalwarp` shell 调用，radar 名含 `` ` `` 即注入）。
- GREEN：3/3 passed（argv 传入、`check=True`、文件名不含引号/分号）。

### FU-02（SSRF）
- RED：9 类绕过形态（十进制/八进制 IP、DNS 名指向 169.254.169.254、0.0.0.0、IPv4-mapped IPv6 等）+ MUSIC allow_private 旁路 → 失败。
- GREEN：21/21 passed（含每跳重定向再校验、不可解析拒绝、方案白名单）。

### FU-03（Sigmet 负 nbins 越界写）
- RED：测试打印 PASSED 但进程以 **SIGSEGV（exit 139）** 退出——堆损坏独立于断言自证。
- GREEN：钳位后 5/5 passed；-1 缺失射线语义与正常 masking 语义回归保持。

### FU-04（KDP 低通滤波 OOB 读）
- RED：ngates=2/3 时静默通过（stencil 越界读）。
- GREEN：7/7 passed（ValueError 守卫 + 三字典返回形态回归）。

### FU-06（max_cappi 路径穿越）
- RED：`radar_name="d/../../evil"` 时文件写出 savedir 之外（`.../evil_20000101000000.png`）。
- GREEN：4/4 passed（3 种穿越 + 良性用例；输出被约束在 savedir 内）。

## 3. 全量验证

| 项目 | 结果 |
|---|---|
| 全量测试（`pytest pyart`） | **80 passed, 4 skipped**（修复前基线 31 passed / 4 skipped；4 个 skip 为 RSL 依赖的既有跳过，与本次无关） |
| 六个安全测试文件合计 | **49 passed** |
| 生成物一致性（.pyx→.c→.so） | 三个 Cython 模块时间戳均为 .pyx < .c < .so；三份 .c 中均含对应 FU 锚点注释（FU-03/FU-04/FU-05），证明 .c 由修复后 .pyx 重新生成 |
| 变更集清理 | `build_ext` 顺带再生成的 3 个无关 .c（`_fast_edge_finder.c`、`_unwrap_1d.c`、`_gate_to_grid_map.c`）经 diff 核实仅为环境差异（fork 作者的 Windows numpy 路径 + Cython 版本空白差异），已 `git checkout` 还原，保证评审 diff 最小化 |

最终变更集（`git status`）：
- 修改：`pyart/graph/max_cappi.py`、`pyart/io/output_to_geotiff.py`、`pyart/io/remote.py`、`pyart/retrieve/kdp_proc.py`、`pyart/io/_sigmetfile.pyx(+.c)`、`pyart/retrieve/_kdp_proc.pyx(+.c)`、`pyart/map/ckdtree.pyx(+.c)`
- 新增：`pyart/io/tests/test_output_to_geotiff_security.py`、`pyart/io/tests/test_remote_ssrf.py`、`pyart/io/tests/test_sigmetfile_nbins_security.py`、`pyart/retrieve/tests/test_kdp_proc_security.py`、`pyart/graph/tests/test_max_cappi_security.py`、`pyart/map/tests/test_ckdtree_security.py` + `pyart/map/tests/fu05_fail_malloc_interposer.c`

## 4. 未决项与附注

1. **发现但超出 P0 范围的功能缺陷（建议单独立项）**：本 fork 内置的 `cKDTree.query()` 中 `retshape = sh[len(sh)-1]` 取错轴（应为 `sh[:-1]`），导致除"查询点数恰等于维度数"外的所有单/多点查询抛 `ValueError: cannot reshape ...`（上游 SciPy 早已修复）。该缺陷影响 `pyart/map/gate_mapper.py`（`query(new_points)`，new_points 形状 (N,3)，N≠3 即失败）。FU-05 的安全测试已因此显式跳过 query 调用。
2. FU-05 安全测试依赖 Linux/glibc/gcc；在不满足条件的环境自动 skip（平台门控 + 编译器探测），不影响其他测试收集。
3. P1/P2 波次（资源耗尽、句柄泄漏、断言可达性等 34 个 FU）尚未启动；建议按计划第 6 章排序继续。
4. 修复均为最小侵入式，未改变任何公开 API 签名或正常路径语义；FU-04 的 ValueError 守卫对合法输入（ngates ≥ 4）零影响。
