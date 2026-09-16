# README 代码示例补编

---

## §4.1 读取雷达基数据 — 统一签名与调用示例

### 读取器统一签名

```python
read_cinrad(filename, radius=460, station=None, use_standard=True,
            align_gates=True, band=None, reader=None)
read_xband(filename, radius=150, station=None, align_gates=True)
read_pa(filename, radius=150, station=None, align_gates=True)
read_mocmosaic(filename, product=None)
read_c98d(filename, **kwargs)          # kwargs 转发至 c98dfile_archive
read_sband_radar(filename, **kwargs)   # kwargs 转发至 read_sband_archive
```

### 调用示例

```python
import pyart

# CINRAD 通用读取:PyCINRAD 优先,失败回退 pycwr.
# band 需显式传入,用于选择默认半径并写入 radar.metadata['radar_band']
radar = pyart.io.read_cinrad("Z9001_20240601000000.bin", band="S")

# X 波段相控阵(AXPT/DXK/XAD/XCD/XSP 文件名自动分派,默认 150 km)
radar = pyart.io.read_xband("Z_RADR_I_ZS409_AXPT0364.bin")
assert radar.metadata["radar_band"] == "X"

# C 波段 C98D 二进制归档(默认 230 km)
radar = pyart.io.read_c98d("C98D_20240601000000.arch")
assert radar.metadata["radar_band"] == "C"

# S 波段 CINRAD-SA(默认 460 km)
radar = pyart.io.read_sband_radar("CINRAD-SA_20240601000000.bin")
assert radar.metadata["radar_band"] == "S"
```

---

## §4.2 双偏振处理 — 统一签名与调用示例

### 统一签名

```python
cband_sband.calibrate_dualpol(radar)
cband_sband.process_phi_kdp(radar, offset=0.0, **kwargs)

phase_proc.det_sys_phase(radar, ncp_lev=0.4, rhohv_lev=0.6,
                         ncp_field=None, rhv_field=None, phidp_field=None)

phase_proc.correct_sys_phase(radar, phi0,
                             phi_name='corrected_differential_phase',
                             phidp_field=None)

bias_and_noise.selfconsistency_bias(radar, zdr_kdpzh_dict)
```

### 调用示例

```python
import pyart
from pyart.correct import cband_sband, phase_proc, bias_and_noise

# 前置:通过读取器获得 radar 对象(任选一种)
radar = pyart.io.read_cinrad("Z9001_20240601000000.bin", band="S")
# radar = pyart.io.read_xband("Z_RADR_I_ZS409_AXPT0364.bin")
# radar = pyart.io.read_c98d("C98D_20240601000000.arch")
# radar = pyart.io.read_sband_radar("CINRAD-SA_20240601000000.bin")

# 按 radar_band 自动取参标定(无 band= 参数)
calibrated = cband_sband.calibrate_dualpol(radar)
kdp = cband_sband.process_phi_kdp(radar, offset=0.0)

# MeteoSwiss 系统相位:先检测,再订正
sys_phase = phase_proc.det_sys_phase(radar)
if sys_phase is not None:
    corrected = phase_proc.correct_sys_phase(radar, phi0=sys_phase)
    # 订正后字段:'corrected_differential_phase'

# 自洽性订正(Gourley 2006)
bias = bias_and_noise.selfconsistency_bias(radar, zdr_kdpzh_dict={})
```

---

## §4.3 生成 GIF 动画 — 完整签名与调用示例

### 完整签名

```python
animate_ppi(radars_or_files, field, sweep=0, out='ppi.gif', vmin=None,
            vmax=None, fps=4, title_fmt=None, gatefilter=None,
            display_kwargs=None, template=None)

animate_rhi(radars_or_files, field, azimuth=None, out='rhi.gif',
            vmin=None, vmax=None, fps=4, title_fmt=None,
            display_kwargs=None)

animate_map_ppi(radars_or_files, field, sweep=0, out='map.gif', vmin=None,
                vmax=None, fps=4, title_fmt=None, projection=None,
                extent=None, display_kwargs=None, cmap=None,
                resolution='110m', mask_outside=False, lat_lines=None,
                lon_lines=None, min_lon=None, max_lon=None, min_lat=None,
                max_lat=None, raster=False, gatefilter=None,
                shapefile=None, draw_coastline=True, draw_borders=True,
                show_timestamp=False, timestamp_fmt='%Y-%m-%d %H:%M UTC')

animate_map_timespan(source, site, start, end, step, field='reflectivity',
                     sweep=0, out='timespan.gif', fps=4, cmap=None,
                     resolution='110m', mask_outside=False, lat_lines=None,
                     lon_lines=None, min_lon=None, max_lon=None,
                     min_lat=None, max_lat=None, draw_coastline=True,
                     draw_borders=True, show_timestamp=True,
                     timestamp_fmt='%Y-%m-%d %H:%M UTC',
                     title_fmt='{site} {time}', template=None,
                     gatefilter=None, share_colorbar=True,
                     display_kwargs=None)

animate_ppi_batch(files, field, out_dir='.', sweep=0, fps=4,
                  template=None, **kwargs)

animate_multi_band(radars_by_band, field, out='multi_band.gif', sweep=0,
                   vmin=None, vmax=None, fps=4, title_fmt=None,
                   display_kwargs=None, share_colorbar=True)
```

### 调用示例

```python
from datetime import datetime, timedelta

import pyart
from pyart.graph import (
    animate_ppi, animate_rhi, animate_map_ppi,
    animate_map_timespan, animate_ppi_batch, animate_multi_band,
)

# 1. 从已加载 Radar 列表生成 PPI 动画
radars = [pyart.io.read_cinrad(f"frame{i}.bin", band="S") for i in range(3)]
animate_ppi(radars, "reflectivity", out="ppi.gif")

# 2. 批量处理文件(glob 或列表,返回 {'success': [...], 'failed': [...]})
report = animate_ppi_batch("data/*.bin", "reflectivity", template="dualpol")

# 3. 远程时间跨度动画(天擎 MUSIC,需凭据)
animate_map_timespan(
    "cma_music", "Z9001",
    start=datetime(2024, 6, 1, 0, 0, 0),
    end=datetime(2024, 6, 1, 1, 0, 0),
    step=timedelta(minutes=5),
    field="reflectivity",
)

# 4. 三波段并排对比(共享色标)
animate_multi_band(
    {"S": s_radar, "C": c_radar, "X": x_radar},
    "reflectivity", out="xsc_compare.gif",
)
```

---

## §4.4 拉取远程数据 — 统一签名与调用示例

### 统一签名

```python
read_time_span(source, site, start, end, step, **kwargs) -> list
get_source(name: str, **kwargs) -> RadarSource
list_sources() -> list
```

### 调用示例

```python
from datetime import datetime, timedelta
from pyart.io import get_source, list_sources, read_time_span

# 5 个数据源(无需网络)
print(list_sources())  # ['cine', 'cma_mos', 'cma_music', 'nexrad', 'nmc_cn']

# 天擎 MUSIC(需环境变量 CMA_MUSIC_USER_ID / CMA_MUSIC_API_KEY,
# 可选 CMA_MUSIC_SERVER_ID,默认 NMIC_MUSIC_CMADAAS)
# 缺凭据时会抛认证错误,这是预期行为
radars = read_time_span(
    "cma_music", "Z9001",
    start=datetime(2024, 6, 1, 0, 0, 0),
    end=datetime(2024, 6, 1, 1, 0, 0),
    step=timedelta(minutes=5),
)

# NEXRAD(匿名 AWS S3,无需凭据,首次访问会下载数据)
radars = read_time_span("nexrad", "KTLX",
    start=datetime(2024, 6, 1, 0, 0, 0),
    end=datetime(2024, 6, 1, 1, 0, 0),
    step=timedelta(minutes=5),
)
```

---

## §4.5 网格产品与格点绘图 — 调用示例

```python
import pyart
from pyart.graph import GridMapDisplay

# 1. 读取 CINRAD 复合产品(返回 Grid 而非 Radar)
grid = pyart.io.read_mocmosaic("mosaic.h5", product="CR")

# 2. 笛卡尔网格映射(单/多部雷达)
from pyart.map import grid_mapper
grid = grid_mapper.map_to_grid(radars,
    grid_shape=(40, 401, 401),
    grid_limits=((0, 20000), (-200000, 200000), (-200000, 200000)),
)

# 3. CAPPI(等高平面位置显示)
from pyart.retrieve import create_cappi
cappi = create_cappi(radar, 2000, field="reflectivity")  # 2000 m

# 4. 复合反射率(上游与本 fork 两套)
from pyart.retrieve import composite_reflectivity      # 上游,参数: radar
from pyart.retrieve.cinrad_products import composite_reflectivity  # 本 fork,参数: filename
```

---

## §8 故障排查 — 代码示例补编

### composite_reflectivity 参数错误

```python
import pyart

# 情况 A:已有 Radar 对象(来自任何读取器)——用上游 API,参数是 radar
from pyart.retrieve import composite_reflectivity
radar = pyart.io.read_cinrad("Z9001.bin", band="S")
out = composite_reflectivity(radar)

# 情况 B:仅有文件路径——用本 fork 子模块 API,参数是 filename
from pyart.retrieve.cinrad_products import composite_reflectivity
out = composite_reflectivity("Z9001.bin")
```

### mocmosaic 复合产品返回 Grid 而非 Radar

```python
import pyart

obj = pyart.io.read_mocmosaic("mosaic.h5", product="CR")
# obj 是 Grid 对象,需用 GridMapDisplay 绘图,而非 RadarDisplay
```
