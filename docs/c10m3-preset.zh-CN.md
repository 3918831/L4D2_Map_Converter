# C10M3 夜晚预设

预设 ID `c10m3-night-v1`，schema 3，使用通用 `generic-replace-v3` 入口。提取参考图 `c10m3_ranchhouse` 的常态夜间环境；原始 C2M1 和 C5M1 两个输入均已完成机器流程并获用户画面验收。详情见 [验证记录](validation.md)，通过范围限于已测样本的实际观察，不扩大为任意地图、全路线或全部模式通过。

## 参数来源与选择

基础 BSP 和 h/l/s 模式补丁逐项比较：所选角色的全部预设字段及六条普通曝光初始化指令一致，比较包括光照方向。只读实体清单未发现 vscripts 字段；这不证明外部插件或任意运行时写入不存在。

| 参数组 | 固定值或策略 |
|---|---|
| 天空 | `sky_day01_09_hdr`，来自原图实际资源名；名称含 day 不代表本预设是白天 |
| 主光 | `52 101 124 10`，yaw 148、pitch -65、SunSpreadAngle 0.5 |
| 环境光 | `18 72 99 2`；主光/环境光 HDR scale 均 0.7，保留 HDR 回退标记 |
| 额外方向光 | `22 102 146 1`，yaw 150、pitch -90、HDR scale .7、spread 0 |
| 阴影 | RGB `3 4 10`，angles `80 180 0`、distance 70，允许局部光阴影 |
| 世界雾 | RGB `14 61 86`、start 128、end 2990、density 1、farz 3000、HDRColorScale .6、lerp 4 |
| 天空雾 | 同色，start -64、end 3000、density 1、HDRColorScale .6 |
| 调色 | `materials/correction/smalltown_ranchhouse.raw`；checkpoint 角色另存 `checkpoint.raw` |
| 默认后处理 | 实际 master `postprocess`：vignette .8→3、contrast -0.2、edges -0.7、grain 1、fade 2 |
| 全局曝光初始化 | min 0.25、max 7、rate 0.5；Bloom scale 1、exponent 2、saturation 1 |
| 太阳光斑 | 原图无 env_sun，预设 null；按既有保护规则移除来源太阳实体 |
| 风 | 原图无 env_wind，预设 null；v3 将来源已有风控制器置为 calm 并保留生命周期，没有则不新增 |

以下属于明确的规范化选择，不冒充原图显式参数：缺失的 height-fog 字段补为 start/density 0、maxdensity 1；缺失 fadetoblackstrength 补 0；没有独立开局调色，color_intro 使用主调色。bright_pixels 为 null，不借用 C5M1 参数。参考图没有感染者/ghost 专用曝光控制器，本预设将已观察到的普通全局数值作为这两个角色的显式默认值，合作模式验收不代表其视角已验证。

选择 master 雾作为覆盖参数；fog_interior 字段保存参考图 church 雾（end 2800），它是参考局部角色，并不意味着通用流程可以识别所有输入图的教堂或室内。沿用现有强覆盖策略，所有普通雾、后处理、调色区域使用常态室外值；天空位置/旋转/scale 和原有空间布局保留。

## 环境声与范围

从 `smalltown_03.woods_* / graveyard → rural.woods3` 提取非定位底噪 `ambient/atmosphere/crucial_town_ambience.wav`，volume .3、pitch 100。house 引用增益 .3，室内数据记录为 .09；当前通用入口所有 soundscape 区域仍使用 outdoor 定义，不做自动分区。

未导入带随机空间位置和特殊播放前缀的蟋蟀循环、随机鸟/昆虫/风阵、远处战斗声、定点汽车火焰声及木板吱响。不复制参考声音脚本全文或场景坐标；音频与天空/调色仍从使用者安装的完整版游戏解析。

不增加参考图的房屋灯、火灾、坟墓、植物、局部雾粒子、机关或事件；输入图局部灯光和材质默认保留。因此原图强照明的加油站、招牌等在夜景中可能更显眼，这与整体月光变暗可以同时发生。既有资源目录处理可识别的源天气/闪电，未知粒子和事件音按现有规则保留并报告。

## 使用与验证

复制 `examples/generic-c10m3.example.json`，填入本机完整游戏、输入 BSP、工具包及独立输出目录，按 [通用指南](generic-conversion.zh-CN.md) 执行 check → build → auto-capture，再验收最终包。转换不需要运行时读取 C10M3 BSP；它只在本次预设提取时作为参考。预设中的 `supported_sources: []` 表示不开放旧专用 source_profile 入口，不是通用输入白名单为空。

首轮重点检查夜间可读性、室内外受光过渡、车辆与手持物反射，以及旧太阳光斑是否已移除。最终 VPK 纳入本地录制资料库，停用同图包并完全重启后使用原安装地图；不要同时启用多个同图风格包。
