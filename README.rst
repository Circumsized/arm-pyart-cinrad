.. -*- mode: rst -*-

Python ARM 雷达工具包 (Py-ART · 国内 X/S/C 双偏振增强版)
==========================================================

|GithubCI| |CodeCovStatus|

|AnacondaCloud| |PyPiDownloads| |CondaDownloads|

|DocsUsers| |DocsGuides|

|ARM| |Tweet|

.. |GithubCI| image:: https://github.com/ARM-DOE/pyart/actions/workflows/ci.yml/badge.svg
    :target: https://github.com/ARM-DOE/pyart/actions?query=workflow%3ACI

.. |CodeCovStatus| image:: https://img.shields.io/codecov/c/github/ARM-DOE/pyart.svg?logo=codecov
    :target: https://codecov.io/gh/ARM-DOE/pyart

.. |AnacondaCloud| image:: https://anaconda.org/conda-forge/arm_pyart/badges/version.svg
    :target: https://anaconda.org/conda-forge/arm_pyart

.. |PyPiDownloads| image:: https://img.shields.io/pypi/dm/arm_pyart.svg
    :target: https://pypi.org/project/arm-pyart/

.. |CondaDownloads| image:: https://anaconda.org/conda-forge/arm_pyart/badges/downloads.svg
    :target: https://anaconda.org/conda-forge/arm_pyart/files

.. |DocsUsers| image:: https://img.shields.io/badge/docs-users-4088b8.svg
    :target: http://arm-doe.github.io/pyart/API/index.html

.. |DocsGuides| image:: https://img.shields.io/badge/docs-guides-4088b8.svg
    :target: https://github.com/ARM-DOE/pyart/tree/main/guides/

.. |ARM| image:: https://img.shields.io/badge/Sponsor-ARM-blue.svg?colorA=00c1de&colorB=00539c
    :target: https://www.arm.gov/

.. |Tweet| image:: https://img.shields.io/twitter/url/http/shields.io.svg?style=social
    :target: https://twitter.com/Py_ART

在 `ARM-DOE/pyart <https://github.com/ARM-DOE/pyart>`_ 的基础上 fork,针对中国新一代
天气雷达(CINRAD)和双偏振业务需求做了扩展。直接对比请看
**与上游 ARM-DOE/pyart 的差异** 一节。

.. important::

   本仓库存在两份 README,内容以 **README.md** 为准:

   - ``README.md`` —— 权威版本,已作为包的 PyPI 长描述
     (见 ``pyproject.toml`` 的 ``[tool.setuptools.dynamic] readme``),
     含完整的 API 清单、实测测试基线与安装说明。
   - ``README.rst`` —— 本文件,保留 RST 渲染与上游目录结构,
     面向习惯 RST 工具链的读者;其中与 README.md 冲突之处一律以 .md 为准。

.. warning::

   本 fork 与上游共用包名 ``arm_pyart``,但**未发布到 PyPI / conda-forge**。
   ``pip install arm_pyart`` 或 ``conda install -c conda-forge arm_pyart``
   只会安装**上游**版本,不含本项目的任何增强功能。请从源码安装,详见
   `从源码安装`_ 一节。


目录
====

- 项目简介_
- 重要链接_
- 引用_
- **与上游 ARM-DOE/pyart 的差异**
- 安装_
- 配置_
- 国内雷达数据 (X/S/C 波段)_
- 远程雷达数据源_
- **GIF 动画**
- 扩展依赖矩阵_
- 从源码安装_
- 开发_


项目简介
=========

Python ARM 雷达工具包 (Py-ART) 是
`Atmospheric Radiation Measurement (ARM) User Facility
<http://www.arm.gov>`_ 维护的开源 Python 模块,基于 Scientific Python 栈,采用
3-Clause BSD 协议发布,主要用于处理降水/云雷达数据。

本 fork 在上游之上新增:

- CINRAD/WSR98D 基数据读取,覆盖 X/S/C 三波段、相控阵、复合产品
- X/S/C 三波段统一的双偏振标定与 Φ-KDP 处理流水线
- 来自 MeteoSwiss 的系统相位检测/平滑与降水相态分类
- 参考 zssherman/pyart_animation 的"时间跨度 → 本地缓存 → 读取为 Radar"统一接入模式
- 国内数据源(天擎 MUSIC、CMA 省级镜像、nmc.cn 合成图)的远程拉取


重要链接
=========

- 上游官方源码仓库: https://github.com/ARM-DOE/pyart
- HTML 文档: https://arm-doe.github.io/pyart/
- 示例: https://arm-doe.github.io/pyart/examples
- 邮件列表: https://openradar.discourse.group/tag/py-art
- 问题追踪: https://github.com/ARM-DOE/pyart/issues


引用
=====

如果本 fork 帮到了你,请同时引用上游论文和具体算法文献::

    Helmus, J.J. & Collis, S.M., (2016). The Python ARM Radar Toolkit
    (Py-ART), a Library for Working with Weather Radar Data in the Python
    Programming Language. Journal of Open Research Software. 4(1), p.e25.
    DOI: http://doi.org/10.5334/jors.119

Φ-KDP、相态分类、QPE、MeteoSwiss 系统相位等算法的原始文献在对应函数 docstring 的
References 段落里。


与上游 ARM-DOE/pyart 的差异
============================

整体定位
--------

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - 维度
     - 上游 ARM-DOE/pyart
     - 本项目
   * - 适用场景
     - ARM 在 X/Ka/W 波段的多平台雷达科研
     - 上游全部 + 中国 X/S/C 三波段业务/科研
   * - 数据来源
     - ARM NetCDF、cfRadial、ODIM_H5、NEXRAD、RSL 等
     - 上游全部 + CINRAD 基数据、复合产品、天擎 MUSIC、CMA 省级镜像、nmc.cn
   * - 双偏振处理
     - 通用 ``correct_*`` / ``retrieve_*``
     - 上游全部 + X/S/C 三波段统一流水线(``BAND_PARAMS`` 驱动)+ MeteoSwiss 系统相位/自洽性
   * - 动画/可视化
     - ``pyart.graph`` 基础绘图
     - 上游全部 + 完整 GIF 流水线(PPI/RHI/地图/批处理/多波段)
   * - 远程获取
     - 仅示例性 AWS 匿名 S3
     - 通用 ``pyart.io.remote`` 抽象层 + 5 个数据源实现
   * - 工程要求
     - 一般科研项目
     - 加固:SSRF 防护、路径穿越防护、原子写、模板占位符安全、流式 GIF 编码、回归测试

差异速览表
----------

.. list-table::
   :header-rows: 1
   :widths: 22 50 14 14

   * - 类别
     - 关键 API/模块
     - 上游
     - 本项目
   * - 读 CINRAD
     - ``pyart.io.read_cinrad``
     - 无
     - 有,PyCINRAD + pycwr 双后端,按 ``band`` 自动选半径
   * - 读 X 波段
     - ``pyart.io.read_xband``
     - 无
     - 有,按文件名分发 AXPT/DXK/XAD/XCD/XSP
   * - 读相控阵
     - ``pyart.io.read_pa``
     - 无
     - 有,CINRAD/SA/PA 数据
   * - 读复合产品
     - ``pyart.io.read_mocmosaic``
     - 无
     - 有,MocMosaic / ACHN (CREF/ET/VIL)
   * - 读 C98D
     - ``pyart.io.read_c98d``
     - 无
     - 有,C 波段 C98D 二进制
   * - 读 S 波段 CINRAD
     - ``pyart.io.read_sband_radar``
     - 无
     - 有,内置 CINRAD-SA 读取器
   * - 实验性 X 波段
     - ``pyart.io.read_xband_724xsp`` / ``read_xband_scrxd01``
     - 无
     - 有,非标 X 波段读取器
   * - 自动识别
     - ``pyart.io.read``
     - 通用
     - 扩展了 CINRAD 自动识别分支
   * - 三波段双偏振
     - ``pyart.correct.cband_sband``
     - 无
     - 有,``BAND_PARAMS`` 驱动 X/S/C 标定 + Φ-KDP
   * - MeteoSwiss ΦDP
     - ``pyart.correct.phase_proc``
     - 部分
     - 有,``det_sys_phase_ray``、``correct_sys_phase``、``smooth_phidp_*``、自洽性 ``selfconsistency_*``
   * - QPE
     - ``pyart.retrieve.qpe``
     - Z/Z-KDP 基础
     - 有,7 族估计:``est_rain_rate_z`` / ``_zpoly`` / ``_kdp`` / ``_zkdp`` /
       ``_a`` / ``_za`` / ``_hydro``
   * - 相态分类
     - ``pyart.retrieve.cinrad_products``
     - 无
     - 有,复刻 PyCINRAD 的水凝物分类
   * - 复合产品
     - ``pyart.retrieve.cinrad_products``
     - 无
     - 有,``composite_reflectivity`` / ``echo_tops`` / ``VIL`` / ``CAPPI``
   * - PPI 动画
     - ``pyart.graph.animate_ppi``
     - 无
     - 有,``template`` 预设 + 流式 GIF 编码
   * - RHI 动画
     - ``pyart.graph.animate_rhi``
     - 无
     - 有
   * - 地图动画
     - ``pyart.graph.animate_map_ppi``
     - 无
     - 有
   * - 时间跨度动画
     - ``pyart.graph.animate_map_timespan``
     - 无
     - 有,直接拉取 + 渲染(zssherman 模式)
   * - 批量动画
     - ``pyart.graph.animate_ppi_batch``
     - 无
     - 有,报告式 ``success/failed`` 统计
   * - 多波段对比
     - ``pyart.graph.animate_multi_band``
     - 无
     - 有,S/C/X 并排,共享色标
   * - 远程层
     - ``pyart.io.remote``
     - 无
     - 有,5 个数据源(nexrad/cma_music/cma_mos/nmc_cn/cine)
   * - 时间跨度助手
     - ``pyart.io.read_time_span``
     - 无
     - 有,zssherman 风格的统一接口
   * - 注册机制
     - ``pyart.io.{register_source,list_sources,get_source}``
     - 无
     - 有,插件式扩展
   * - 配置自检
     - ``scripts/verify_remote_sources.py``
     - 无
     - 有,**离线**自检凭据/可选依赖/端点格式,不发网络请求
   * - 安全性
     - –
     - 不涉及
     - 有,``_validate_url`` 防 SSRF、``cache_path`` 防路径穿越、原子写
   * - 测试
     - 通用
     - 通用
     - 有,新增 io/remote、io/cinrad、graph/animation、correct/phase_proc、correct/bias 等专项回归测试

IO 读取层
---------

上游 ``pyart.io`` 主要面向 cfradial、NEXRAD、ODIM_H5、UF、RSL、ARM、cfRadial2 等
"国际通用格式",并提供 ``read`` 通用入口。

本项目新增 5 类对中国雷达的读取能力,并把它们全部接入 ``pyart.io.read`` 的自动识别
流程:

- ``read_cinrad(filename, band='S', ...)``: 复刻 PyCINRAD 的标准数据 → Py-ART Radar
  流程,带 pycwr 回退。``band='S' / 'C' / 'X'`` 自动选 460 / 230 / 150 km 默认半径。
- ``read_xband(filename, ...)``: 按文件名模式分发 X 波段(AXPT/DXK/XAD/XCD/XSP),
  共享 PyCINRAD + pycwr 后端链。
- ``read_pa(filename, ...)``: CINRAD/SA/PA 相控阵数据。
- ``read_mocmosaic(filename, product=None)``: MocMosaic / ACHN 复合产品(CREF/ET/VIL)。
- ``read_c98d(filename, ...)`` / ``c98dfile_archive``: C 波段 C98D 二进制归档。
- ``read_sband_radar(filename)`` / ``read_sband_archive``: S 波段 CINRAD-SA 内置
  读取器。
- ``read_xband_724xsp`` / ``read_xband_scrxd01``: 实验性的非标准 X 波段读取器。
- ``read`` 自动识别: 增加了 CINRAD 分支。``ImportError``/真实读错误不再被静默吞掉,
  失败时直接抛出原异常。

后端双链路(PyCINRAD + pycwr)保证现场环境差异下的鲁棒性。

双偏振订正与产品层
------------------

上游 ``pyart.correct`` 和 ``pyart.retrieve`` 是通用科研算法集,缺一条"按波段参数
统一驱动 + 业务级产品"的主线。

本项目在这一层做了如下补强:

- ``pyart.correct.cband_sband``: 提供
  ``BAND_PARAMS = {'X': {...}, 'C': {...}, 'S': {...}}``,
  ``calibrate_dualpol`` 和 ``process_phi_kdp`` 一次调用即按波段取参数,业务脚本里不再
  到处 if/else。
- ``pyart.correct.phase_proc``: 复刻并改进 MeteoSwiss 系算法。

  - ``det_sys_phase`` / ``det_sys_phase_ray`` / ``det_sys_phase_gf``: 系统相位检测
    (径向版与 gatefilter 版)。
  - ``correct_sys_phase``: 系统相位订正,保留掩码,不丢数据。
  - ``smooth_phidp_single_window`` / ``smooth_phidp_double_window``: 单/双窗平滑。
  - ``construct_A_matrix`` / ``LP_solver_cvxopt`` / ``LP_solver_pyglpk`` /
    ``LP_solver_cylp`` / ``phase_proc_lp`` / ``phase_proc_lp_gf``: 完整 LP 相位
    处理流水线。

- ``pyart.correct.bias_and_noise``: 新增 ``est_rhohv_rain``、``est_zdr_precip``、
  ``est_zdr_snow``、``selfconsistency_bias / bias2 / kdp_phidp / zdr_zh`` 等降水
  相态与自洽性工具。
- ``pyart.retrieve.cinrad_products``: 复刻 PyCINRAD 业务产品。

  - ``composite_reflectivity`` / ``echo_tops`` / ``vert_integrated_liquid`` /
    ``cappi``: 业务拼图/回波顶/垂直积分液态水/等高 CAPPI。
  - ``hydro_class``: 基于 PyCINRAD 的水凝物分类(``method='hybrid'``)。

- ``pyart.retrieve.qpe``: QPE 估计族共 7 个 —— ``est_rain_rate_z`` (Z-R)、
  ``est_rain_rate_zpoly`` (Z 多项式)、``est_rain_rate_kdp`` (KDP)、
  ``est_rain_rate_zkdp`` (Z-KDP)、``est_rain_rate_a`` (A-rain)、
  ``est_rain_rate_za`` (Z-A)、``est_rain_rate_hydro`` (Hydro-method)。
  早期文档中提到的 "Z-KDP-A" 族在代码中并不存在。

GIF 动画与绘图层
----------------

上游 ``pyart.graph`` 以单帧 PPI/RHI 绘制为主,没有完整的动画管线。

本项目在 ``pyart/graph/animation.py`` 新增 6 类动画能力:

- ``animate_ppi``: 动画 PPI 扫描。支持 ``template`` 预设(``dualpol`` /
  ``timeseries``),可预置字段、图形尺寸和标题格式;采用 imageio 流式编码,不把全部
  帧驻留内存。
- ``animate_rhi``: 动画 RHI 剖面,按方位角或扫描索引选取。
- ``animate_map_ppi``: 在 cartopy 地图上动画 PPI。
- ``animate_map_timespan``: 拉取一段时间跨度直接渲染地图动画,复刻
  zssherman/pyart_animation 风格。
- ``animate_ppi_batch``: 批量从列表/glob 生成多份 GIF,返回
  ``{'success': [...], 'failed': [...]}`` 报告。
- ``animate_multi_band``: S/C/X 三波段并排对比,共享色标/色界。

附带的修复:``radardisplay.plot_range_ring_range`` 改为真正的环形带(annulus
polygon),不再画成扇形带;模板标题占位符加 ``string.Formatter`` 防御,缺失字段不再
触发 ``KeyError``。

远程数据源层
------------

上游只有示例性 AWS 脚本。本项目引入了与数据源无关的抽象层:

- ``pyart.io.remote.RadarSource``: Protocol 抽象。
- ``pyart.io.remote._BaseSource``: 复用 HTTP/缓存/退避重试。
- 5 个具体数据源实现:

  - ``NexradSource``: 公共匿名 AWS S3,NEXRAD Level II。
  - ``CmaMusicSource``: 中国气象局 **天擎 MUSIC**,X/S/C Level-2 双偏振基数据。
    **当前唯一真实可拉 CINRAD Level-2 的公共服务**。
  - ``CmaMosSource``: 各省 CMA 镜像,要求注入 ``station_config``,不内置无验证的
    URL 模板。
  - ``NmcCnSource``: nmc.cn 中央台,只服务栅格合成 PNG,不服务偏振体扫。
  - ``CineSource``: 本地 CINRAD/CINE 文件树离线索引,提供 ``scan_cache()`` 批量
    转换。

- 顶层助手 ``pyart.io.read_time_span(source, site, start, end, step)``。
- 注册/查询 API: ``register_source`` / ``list_sources`` / ``get_source``。
- 真实端点自检: ``scripts/verify_remote_sources.py``。

工程质量硬化
------------

v1.x 期间完成了一轮全量代码评审(21 项发现,按严重度分级修复),主要动作:

- **CR-001** ``animation._apply_template`` 不再把 ``figsize`` / ``title_fmt`` 泄漏
  到 plot kwargs。
- **CR-002** ``pyart.io.read`` 不再对 CINRAD 文件 double-read,也不再吞掉真实异常。
- **CR-003** ``radardisplay.plot_range_ring_range`` 改成真正的环形 polygon。
- **CR-004** ``cache_path`` 防路径穿越(``/``、``\``、``:`` 全部被替换;
  ``realpath`` 校验)。
- **CR-005** ``_validate_url`` 拒绝私网/loopback/link-local/云元数据地址,提供
  ``allow_private=True`` 逃生口给天擎私有网段。
- **CR-007** ``correct_sys_phase`` 保留掩码,不再用 ``np.asarray`` 丢信息。
- **CR-008** 3 个 HTTP 数据源(``nmc_cn`` / ``cma_music`` / ``cma_mos``)的 ``fetch``
  走原子写(temp + ``os.replace``),杜绝半写状态;``nexrad`` 源走 s3fs 直写、
  ``cine`` 源为本地文件,均不涉及下载写入。
- **CR-010** GIF 编码改用 imageio 流式 ``get_writer`` + ``append_data``,不再
  ``mimwrite`` 整列。
- **CR-011** ``_with_colorbar_units`` 直接扫描 ``mappable.colorbar``,与新版
  matplotlib 兼容。
- **CR-015** BZ2/GZ 解压异常用 ``raise ... from exc``,保留原始堆栈。
- **CR-016** 负 azimuth 被钳到 [0, 360)。
- **CR-017** 双偏振 zip 截断校验,避免坏包后静默回退。

测试覆盖:

- 12 个新增回归测试覆盖上述修复点
- 全量回归实测基线(2026-08-31,Python 3.12 / Windows):
  **1008 条收集,937 通过 / 71 跳过 / 0 失败**
- GIF 烟雾测试(``animate_ppi(template='dualpol')``)成功写出 ``GIF8`` 头

.. note::

   测试已按上游布局迁移到仓库顶层 ``tests/``;包内仅保留少量 fork 专属用例
   (``pyart/io/tests``、``pyart/graph/tests``、``pyart/correct/tests``)。

   **不要用** ``pytest --pyargs pyart`` —— 它只解析已安装的包目录,
   实测仅收集 32 / 1008 条(约 3%)。请在仓库根目录直接执行 ``pytest``。


安装
====

本 fork 支持 Python 3.11 / 3.12 / 3.13(``requires-python = ">=3.11"``),
不再支持 Python 2 / Python 3.10 及以下版本。

.. warning::

   下列 conda / pip 命令安装的是 **上游** ``arm_pyart``,仅含上游能力,
   **不含** 本 fork 的 CINRAD 读取、GIF 动画、远程数据源等增强。
   需要增强功能请跳到 `从源码安装`_。

上游版本(仅上游能力)的新建环境安装::

    # 基于 environment.yml 完整安装
    conda env create -f environment.yml
    conda activate pyart_env

    # 或者只装核心,可选依赖按需取
    conda create -n pyart_env -c conda-forge python=3.13 arm_pyart
    conda activate pyart_env

后续更新::

    conda update -c conda-forge arm_pyart

如果用 mamba,把 ``conda`` 替换为 ``mamba`` 即可。


配置
=====

Py-ART 的配置文件指定默认元数据、字段名、色标、绘图限幅。设置环境变量
**PYART_CONFIG** 指向自定义配置文件即可自动加载。详见 ``pyart.load_config`` 文档。


国内雷达数据 (X/S/C 波段)
==========================

新增的中国天气雷达基数据读取入口,覆盖 X 波段(CINRAD AXPT/DXK 及非标格式)、S 波段
(CINRAD-SA/SB/SC 及前代 WSR-98D)、C 波段(CINRAD-CB/CC/CCJ/CD 及 C98D)的双偏振数据。

逐型号覆盖矩阵
--------------

下表列出 **8 种** 在役/前代 CINRAD 基数据型号的支持状态。数据实测自
``pyart.io.cinrad_bridge`` 与 PyCINRAD 1.9 ``infer_type``,由 49 项 routing
自动化测试锁死(详见 README.md §2.2)。

.. list-table::
   :header-rows: 1
   :widths: 16 30 8 12 26 20

   * - 型号
     - 业务命名样例
     - 波段
     - 默认半径
     - 入口
     - PyCINRAD 1.9 识别
   * - CINRAD/SA
     - ``Z_RADR_I_Z9200_..._O_DOR_SA_CAP.bin``
     - S
     - 460 km
     - ``read_cinrad`` / ``read_sband_radar``
     - 是(magic)
   * - CINRAD/SB
     - ``Z_RADR_I_Z9200_..._O_DOR_SB_CAP.bin``
     - S
     - 460 km
     - ``read_cinrad``
     - 是
   * - CINRAD/SC
     - ``Z_RADR_I_Z9200_..._O_DOR_SC_CAP.bin``
     - S
     - 460 km
     - ``read_cinrad``
     - 是(头部 magic)
   * - CINRAD/CB
     - ``Z_RADR_I_Z9200_..._O_DOR_CB_CAP.bin``
     - C
     - 230 km
     - ``read_cinrad``
     - 是
   * - CINRAD/CC
     - ``Z_RADR_I_Z9200_..._O_DOR_CC_CAP.bin``
     - C
     - 230 km
     - ``read_cinrad``
     - 是(头部 magic)
   * - CINRAD/CCJ
     - ``Z_RADR_I_Z9200_..._O_DOR_CCJ_CAP.bin``
     - C
     - 230 km
     - ``read_cinrad``
     - 是(``spart[7]='CCJ'``)
   * - CINRAD/CD
     - ``Z_RADR_I_Z9200_..._O_DOR_CD_CAP.bin``
     - C
     - 230 km
     - ``read_cinrad``
     - 是(头部 magic)
   * - WSR-98D
     - ``Z_RADR_C_DOPPLER_WSR98D_20190421.bin``
     - S
     - 460 km
     - ``read_cinrad`` (自动检测)
     - 否(桥接层强制 ``radar_type='SA'``)

说明:

- **业务命名样例** 采用 ``Z_RADR_I_<站号>_<时间>_O_DOR_<型号>_CAP.bin`` 模板;
  WSR-98D 为例外,沿用 1998 年历史命名 ``Z_RADR_C_DOPPLER_WSR98D_...``。
- **默认半径** 指 ``read_cinrad(..., band=...)`` 传入 ``band`` 且未显式覆盖
  ``radius`` 时自动选取的公里数。
- **CINRAD/CCJ** 是 CINRAD/CC 的双偏振升级型号,PyCINRAD 1.9 通过下划线
  分隔字段 ``spart[7]`` 单独识别为 ``'CCJ'``;pyart 路由层用 ``'CC' in name``
  子串匹配,会同时命中 CC 与 CCJ。
- **CINRAD/SA 另有专属入口** ``read_sband_radar`` (走 S 波段优化的字节路径),
  与 ``read_cinrad(..., band='S')`` 功能等价但实现路径不同。
- **WSR-98D** 的字节流与 CINRAD-SA 兼容,但 PyCINRAD 1.9 的 ``infer_type``
  **无法** 从历史命名推断型号(返回 ``None``)。pyart 桥接层检测到 ``WSR98D``
  子串后强制走 SA 解码器,并发出 ``RuntimeWarning`` 显式告知,同时在
  ``radar.metadata['original_container']`` 标记 ``'CINRAD-WSR98D'`` 以便追溯。
- **端到端验证状态**: 上表所有型号的"分派到正确 reader 函数"均已由 49 项
  routing 测试锁死;**字节流级解析** 仍待真实样本验证(设置 ``CINRAD_TEST_FILE``
  环境变量后运行 ``pytest pyart/io/tests/test_cinrad_bridge.py``)。

- ``pyart.io.read_cinrad``: 通过 PyCINRAD(``standard_data_to_pyart``)读取 CINRAD
  基数据,带 pycwr 回退。``band='S' / 'C' / 'X'`` 自动选 460 / 230 / 150 km 默认半径。
- ``pyart.io.read_xband``: 按文件名模式(AXPT/DXK/XAD/XCD/XSP)分发 X 波段读取,
  共用 PyCINRAD + pycwr 后端链。
- ``pyart.io.read_pa``: CINRAD 相控阵(AXPT/DXK)标准数据。
- ``pyart.io.read_mocmosaic``: 探测中心合成产品(MocMosaic / ACHN,如 CREF/ET/VIL)。
- ``pyart.io.read_xband_724xsp`` / ``pyart.io.read_xband_scrxd01``: 实验性非标
  X 波段读取器,需用真实样本验证。
- ``pyart.io.read_c98d`` / ``pyart.io.c98dfile_archive``: C 波段 C98D 二进制归档。
- ``pyart.io.read_sband_radar`` / ``pyart.io.read_sband_archive``: S 波段 CINRAD-SA
  内置读取器。

可选依赖(须在本仓库源码目录下执行,不可用 PyPI 包名)::

    pip install ".[cinrad]"      # cinrad + pycwr
    pip install ".[xradar]"      # xradar 互操作


远程雷达数据源
==============

``pyart.io.remote`` 是与数据源无关的获取层,采用 zssherman/pyart_animation 推广的
"列文件 → 下载到本地缓存 → 读为 Radar"模式。已注册的数据源::

    >>> pyart.io.list_sources()
    ['cine', 'cma_mos', 'cma_music', 'nexrad', 'nmc_cn']

- ``nexrad``: 公共匿名 AWS S3 存储桶 ``noaa-nexrad-level2``,NEXRAD Level II。
  ``NexradSource.list_files('KTLX', start, end, step)`` 按 ``step`` 网格点取一卷,
  ``read`` 通过 ``pyart.io.read_nexrad_archive`` 读取。
- ``cma_music``: 中国气象局 **天擎 MUSIC**。当前唯一按站点 + 时间范围返回真实
  CINRAD Level-2 基数据(X/S/C 双偏振)的公共服务。凭据从环境变量
  ``CMA_MUSIC_USER_ID`` / ``CMA_MUSIC_API_KEY`` 读取(可选 ``CMA_MUSIC_SERVER_ID``,
  默认 ``NMIC_MUSIC_CMADAAS``)。``cma_music_api`` 客户端包可选,延迟导入。
- ``cma_mos``: 各省 CMA 镜像。CMA 没公开稳定的匿名镜像布局,**不内置 URL 模板**,
  需通过 ``CmaMosSource(station_config={'code': {'template': 'http://...'}})`` 注入。
  ``list_sites`` 探测可达性并丢弃无法访问的条目。
- ``nmc_cn``: 中央气象台(nmc.cn)公共接口。只服务栅格合成 PNG,不服务偏振体扫。
  ``read()`` 返回本地文件路径,不能用于双偏振处理,仅适合合成图发现/缓存。
- ``cine``: 本地 CINRAD/CINE 文件树完全离线的索引。
  ``CineSource(root=...).scan_cache()`` 批量把每个缓存文件转成 ``Radar``,无需
  访问网络。

顶层助手 ``read_time_span``::

    from pyart.io import read_time_span
    from datetime import datetime, timedelta

    radars = read_time_span('nexrad', 'KTLX',
                            datetime(2024, 6, 1, 0, 0, 0),
                            datetime(2024, 6, 1, 1, 0, 0),
                            timedelta(minutes=5))

远程层可选依赖::

    pip install ".[remote]"      # requests, pooch, s3fs

数据源配置自检(**离线脚本**:不发起网络请求,也不以非零码退出)::

    python scripts/verify_remote_sources.py            # 检查全部数据源
    python scripts/verify_remote_sources.py cma_music  # 仅检查指定源(位置参数)

真实端点验证需另行在具备网络与天擎凭据的环境完成,不在本脚本职责范围内。


GIF 动画
========

``pyart.graph`` 通过 ``imageio`` (``pip install ".[animation]"``)把 PPI / RHI /
地图动画渲染为 GIF。所有动画函数接受 ``Radar`` 列表或文件路径,遵守 ``MAX_FRAMES``
上限(默认 200)。

.. note::

   ``template`` 参数仅 ``animate_ppi`` / ``animate_map_timespan`` /
   ``animate_ppi_batch`` 三个函数接受;``animate_rhi``、``animate_map_ppi``、
   ``animate_multi_band`` 不接受该参数。
   已定义的 ``dualpol_map`` 模板因此暂无消费点。

- ``animate_ppi``: 动画 PPI 扫描,支持 ``template`` 预设(``dualpol`` /
  ``timeseries``)。
- ``animate_rhi``: 动画 RHI 剖面(方位角或扫描索引)。
- ``animate_map_ppi``: cartopy 地图上的 PPI 动画。
- ``animate_map_timespan``: 从远程数据源拉取时间跨度,直接渲染到 cartopy 地图上。
- ``animate_ppi_batch``: 批量从列表/glob 生成多份 GIF,返回
  ``{'success': [...], 'failed': [...]}`` 报告。
- ``animate_multi_band``: S/C/X 并排对比,共享色标。

::

    import pyart
    from pyart.graph import animate_ppi_batch, animate_multi_band
    from pyart.io import read_time_span
    from datetime import datetime, timedelta

    report = animate_ppi_batch("data/*AXPT*.bin", "reflectivity",
                               template="dualpol")

    pyart.graph.animate_map_timespan('cma_music', 'Z9001',
                                     datetime(2024, 6, 1, 0, 0, 0),
                                     datetime(2024, 6, 1, 1, 0, 0),
                                     timedelta(minutes=5),
                                     field='reflectivity')

    animate_multi_band({"S": s_radar, "C": c_radar, "X": x_radar},
                       "reflectivity", out="xsc_compare.gif")


扩展依赖矩阵
============

.. list-table::
   :header-rows: 1
   :widths: 15 30 35

   * - extra
     - 依赖包
     - 提供功能
   * - ``cinrad``
     - ``cinrad`` (PyPI 包名,即 PyCINRAD)、``pycwr>=1.0.8``
     - 国内雷达读取器
   * - ``animation``
     - ``imageio>=2.5.0``
     - GIF 动画编码
   * - ``remote``
     - ``requests>=2.31.0``、``pooch>=1.8.0``、``s3fs>=2023.9.0``
     - 远程雷达数据源
   * - ``xradar``
     - ``xradar>=0.12.0``
     - xradar 数据树互操作
   * - ``full``
     - 以上全部
     - 全部功能

安装命令一律为源码安装形式(在本仓库目录下执行)::

    pip install ".[cinrad]" ".[animation]" ".[remote]" ".[xradar]"
    pip install ".[full]"

``xradar`` 同时是核心依赖(``[project].dependencies`` 已声明),因此开箱可用;
``pooch`` 与 ``s3fs`` 亦为核心依赖,``full`` 组仅为语义聚合。


从源码安装
==========

源码安装适合想跟进未发布版本或做二次开发的用户。

.. warning::

   请 clone **本 fork** 的仓库地址,**不要** clone ``ARM-DOE/pyart`` ——
   上游仓库不含本项目的任何增强功能。请将下方 ``<fork-url>`` 替换为实际地址。

::

    git clone <fork-url>
    cd pyart

    # 基础安装(含上游全部能力)
    pip install .

    # 按需启用扩展
    pip install ".[cinrad]"
    pip install ".[animation]"
    pip install ".[remote]"
    pip install ".[xradar]"
    pip install ".[full]"

    # 开发模式 (推荐,可直接编辑生效)
    pip install -e ".[full]"

.. note::

   直接调用 ``python setup.py install`` / ``python setup.py build`` 已被弃用,
   请使用 ``pip install .``。现代 pip 通过 PEP 517 构建,会读取
   ``pyproject.toml`` 而非直接执行 ``setup.py``。


开发
====

Py-ART 是开源社区项目,欢迎所有人贡献。

获取源码
--------

::

    git clone <fork-url>

要贡献代码,推荐先 fork 仓库(上游为 `ARM-DOE/pyart
<https://github.com/ARM-DOE/pyart>`_;若针对本 fork 的增强功能提交,请使用本 fork
的地址)。

获取帮助
--------

`讨论论坛 <https://github.com/ARM-DOE/pyart/discussions>`_ 可以提问和请求帮助。

贡献流程
--------

许可证采用 BSD 3-Clause,详见 ``LICENSE.txt``。完整贡献流程见
`CONTRIBUTING.rst <https://github.com/ARM-DOE/pyart/blob/main/CONTRIBUTING.rst>`_
。

测试
----

::

    pip install pytest pytest-mpl responses open-radar-data

    # 在仓库根目录直接执行(推荐)
    pytest

.. warning::

   **不要使用** ``pytest --pyargs pyart``:该命令只解析已安装的包目录,
   在本仓库实测仅收集 **32 / 1008** 条用例(约 3%),会严重低估覆盖率。
   绝大多数用例位于仓库顶层 ``tests/``,必须直接执行 ``pytest`` 才能收集。

   ``tests/correct/test_correct_bias.py`` 与 ``tests/xradar/test_accessor.py``
   在收集阶段需联网下载样例数据;在离线或代理受限环境中会被判为
   collection error,需在联网环境下运行。
