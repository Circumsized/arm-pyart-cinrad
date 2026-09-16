# Contributing to Py-ART (国内 X/S/C 双偏振增强 fork)

首先,感谢你愿意为这个 fork 做贡献!本文档说明开发流程、测试与提交规范.

## 1. 开发环境

```bash
git clone <本 fork 的仓库地址>
cd pyart
pip install -e ".[cinrad,animation,remote,xradar]"
pytest -q
```

Python 3.11 / 3.12 / 3.13 均可.**不要**用 `pip install arm_pyart` 安装——那会装上游包,不含本 fork 的任何增强.

## 2. 项目结构

```
pyart/
  io/         # 读取层:CINRAD 桥、NEXRAD、cfRadial、SIGMET...
  correct/    # 双偏振标定:phase_proc / bias_and_noise / cband_sband
  graph/      # 可视化:animation / display / gridmapdisplay
  retrieve/   # 反演:cinrad_products / qvp / vad / cappi
  xradar/     # xradar 互操作
tests/        # 顶层测试(fork 新增的回归测试)
```

Fork 新增的核心模块:
- `pyart/io/remote.py` — 国内远程数据源(天擎 MUSIC / CMA 省级镜像 / nmc.cn / NEXRAD S3 / CINE)
- `pyart/io/cinrad_bridge.py` — CINRAD 基数据桥接(SA/SB/CB/CC/CCJ/SC/CD + WSR-98D)
- `pyart/io/c98d_archive.py` — C 波段 C98D 归档
- `pyart/io/sband_archive.py` — S 波段 CINRAD-SA 归档
- `pyart/io/xband_native.py` — X 波段相控阵
- `pyart/correct/cband_sband.py` — 波段感知双偏振标定(BAND_PARAMS)
- `pyart/graph/animation.py` — GIF 动画管线

## 3. 测试

```bash
# 全量测试(约 6-8 分钟)
pytest -q

# 只跑 fork 新增模块的测试
pytest pyart/io/tests/ pyart/correct/tests/ tests/io/ -q

# 跳过需网络的测试
pytest -q --ignore=tests/correct/test_correct_bias.py --ignore=tests/xradar/test_accessor.py
```

测试基线(2026-08-31 实测):**997+ passed / 71 skipped / 0 failed**.

> [!WARNING]
> 若收集阶段批量报
> `ValueError: numpy.dtype size changed, may indicate binary incompatibility`,
> 说明当前解释器与编译扩展的 numpy ABI 不匹配,需先重建环境:
>
> ```bash
> pip install -e ".[full]" --no-build-isolation --force-reinstall
> ```
>
> 复现前先确认 `python -c "import numpy, pyart"` 无异常。

### 需要真实数据的测试

部分 CINRAD 端到端测试需要真实基数据文件:
```bash
export CINRAD_TEST_FILE=/path/to/real_cinrad_file.bin
pytest pyart/io/tests/test_cinrad_bridge.py -v
```

不设该环境变量时,这些测试自动 skip,不影响基线.

## 4. 提交规范

遵循上游 ARM-DOE/pyart 的 commit message 风格:

- `ENH:` 新功能或增强
- `FIX:` bug 修复
- `DOC:` 文档变更
- `TST:` 测试变更
- `REF:` 重构(无功能变化)
- `MAINT:` 维护性变更(依赖升级等)

示例:`FIX: WSR-98D filename routing in cinrad_bridge`

## 5. Fork 特有的贡献场景

### 5.1 新增 CINRAD 格式支持

1. 在 `pyart/io/cinrad_bridge.py` 的 `_CINRAD_NAME_BAND` 表中添加型号→波段映射
2. 在 `pyart/io/auto_read.py` 的 `cinrad_patterns` 列表中添加文件名关键字
3. 在 `pyart/io/tests/test_cinrad_routing.py` 添加 routing 测试
4. 如有真实样本,设置 `CINRAD_TEST_FILE` 跑端到端测试

### 5.2 新增远程数据源

1. 在 `pyart/io/remote.py` 继承 `_BaseSource`,实现 `list_files` / `fetch` / `list_sites`
2. 用 `@register_source` 注册
3. 在 `tests/io/test_remote_*.py` 添加回归测试

### 5.3 双偏振算法

1. 在 `pyart/correct/cband_sband.py` 的 `BAND_PARAMS` 表中添加新波段参数
2. 用 `radar.metadata['radar_band']` 确保波段传播
3. 在 `pyart/correct/tests/test_dualpol_pipeline.py` 添加管线测试

## 6. 与上游的关系

本 fork 基于 `ARM-DOE/pyart` 上游提交 `5310bd21b7`.Fork 增量为 52 个非生成文件 / +7,536 行(已排除自动生成的 `.c` 文件).

**不要**把 fork 的改动提交到上游——上游不支持 CINRAD 格式,这是 fork 专属能力.

## 7. 许可证

BSD-3-Clause,与上游一致.详见 [LICENSE.txt](LICENSE.txt).

## 8. 引用

使用本 fork 发表论文时,请引用:

- Helmus, J.J. & Collis, S.M., (2016). The Python ARM Radar Toolkit (Py-ART). JORS 4(1), p.e25. DOI: http://doi.org/10.5334/jors.119

Fork 新增的 CINRAD 读取、双偏振标定、远程数据源等能力不改变原始引用,只是扩展了 Py-ART 在中国天气雷达业务场景的适用性.
