# Changelog

所有本 fork 的增量变更记录.上游变更请参考 [ARM-DOE/pyart](https://github.com/ARM-DOE/pyart) 的 release notes.

本 fork 基于上游提交 `5310bd21b7`,累计 14 个增量 commit.

## [Unreleased]

### 已修复但未 commit 的工作区改动

以下变更已在工作区完成,但尚未形成 commit:

#### P0 — 安全与正确性
- `pyart/io/remote.py`: NEXRAD 文件名正则 `_(\d{8})_(\d{6})_V` → `(\d{8})_(\d{6})_V`(去掉前置下划线,真实文件名永不匹配)
- `pyart/io/remote.py`: NEXRAD `fetch` 双重桶前缀 `bucket/bucket/...` → `_s3_path` 归一化
- `pyart/io/remote.py`: `_timespan` `step<=0` 死循环 → 显式 `raise ValueError`
- `pyart/io/remote.py`: `_http_get` 重定向绕过 SSRF → 手动逐跳 re-validate
- `pyart/io/remote.py`: `_atomic_write` 并发竞态 → `tempfile.mkstemp` 独立 tmp 文件
- `pyart/io/cinrad_bridge.py`: WSR-98D 历史命名 `infer_type` 无法识别 → 桥接层强制 `radar_type='SA'`

#### P1 — 契约与可观测性
- `pyart/io/remote.py`: 缓存投毒(残缺文件永久复用)→ `force_refresh=True` 恢复通道
- `pyart/io/remote.py`: `datetime.utcnow()` 弃用告警 → `_utcnow()` 帮助函数
- `pyart/io/remote.py`: `read` 失败静默返回 str → 发出 `RuntimeWarning`
- `pyart/io/remote.py`: `read_time_span` 失败静默跳过 → 聚合告警
- `pyart/graph/animation.py`: `_to_radars` 一次性物化抵消流式声明 → `_iter_radars` + `_PeekedFirst`
- `pyart/io/cinrad_bridge.py`: `_resolve_band` 默认 'C' 静默错处理 → `_infer_band_from_filename`

#### 新增测试
- `pyart/io/tests/test_cinrad_routing.py` — CINRAD 8 型号 routing 自动化测试(22 个测试函数,含 4 组参数化)
- `pyart/correct/tests/test_dualpol_pipeline.py` — 双偏振管线端到端测试(5 个测试函数)
- `tests/io/test_remote_regressions.py` — remote.py P0/P1 修复回归测试(18 个测试函数)

#### 文档
- `README.md` — 核心亮点区新增 CINRAD 8 型号清单
- `README.rst` — 新增逐型号覆盖矩阵(8 型号 list-table)+ 修正 SC 波段归类错误
- `CONTRIBUTING.md` — 新建 fork 专属贡献指南
- `CHANGELOG.md` — 新建(本文件)

## [2.2.5.post13] — 2026-08-30

基于上游 `5310bd21b7` 的 14 个增量 commit.

### Added
- **CINRAD 基数据读取**(5 个读取器): `read_cinrad` / `read_xband` / `read_c98d` / `read_sband_radar` / `read_mocmosaic`,覆盖 S/C/X 三波段、X 波段相控阵、MocMosaic/ACHN 复合产品,双后端冗余(PyCINRAD 优先,pycwr 回退)
- **8 种 CINRAD 型号支持**: SA / SB / CB / CC / CCJ / SC / CD + 前代 WSR-98D,共 8 种基数据格式
- **波段感知双偏振处理**: `BAND_PARAMS` 参数表按 X/C/S 统一驱动标定与 Φ-KDP 处理
- **GIF 动画管线**: 6 个动画函数(PPI / RHI / 地图 / 时间跨度 / 批量 / 三波段对比)× 4 套渲染模板
- **国内远程数据源**: 天擎 MUSIC、CMA 省级镜像、nmc.cn 合成图、NEXRAD S3、本地 CINE 文件树,统一收敛到 `read_time_span` 单一接口
- **MeteoSwiss 系统相位处理**: `correct_sys_phase` + LP 相位处理(cvxopt / pyglpk / cylp 三后端)
- **CINRAD 业务产品**: `retrieve.cinrad_products`(CREF / ET / VIL / hydro_class / QPE 7 族)
- **xradar 互操作**: `to_pyart_radar`(单向)

### Changed
- `pyproject.toml`: 新增 `[cinrad]` / `[animation]` / `[remote]` / `[xradar]` extras
- `setup.py`: 移除 `long_description=`(改由 `pyproject.toml` dynamic readme 驱动)
- `MANIFEST.in`: 新增 `include README.md`
- `pyart/retrieve/__init__.py`: 导出 `cinrad_products` 子模块

### Security
- `pyart/io/remote.py`: URL 白名单 + SSRF 防护(`_validate_url`)
- `pyart/io/remote.py`: 原子写(`_atomic_write`)+ 并发竞态防护
- `pyart/io/remote.py`: 重定向逐跳 re-validate(防止 302 绕过白名单)
- `pyart/io/cinrad_bridge.py`: WSR-98D 桥接层强制 SA decoder + `RuntimeWarning`

### Fixed
- `pyart/io/cinrad_bridge.py`: `read_cinrad` 不写 `radar_band` metadata → `_infer_band_from_filename` 推断
- `pyart/graph/animation.py`: `_to_radars` 伪流式 → `_iter_radars` + `_PeekedFirst`
- `pyart/io/remote.py`: NEXRAD 正则失效 / 双重桶前缀 / step<=0 死循环 / SSRF 重定向绕过 / 并发竞态 / 缓存投毒 / utcnow 弃用

### Test Baseline
- 997+ passed / 71 skipped / 0 failed(Python 3.12 / Windows,约 6-8 分钟)
- 1042 collected(pyart io 584 / graph 82 / core 68 / retrieve 60 / correct 60 / xradar 43 / map 40 / filters 35 / util 25 / 其他 9)
