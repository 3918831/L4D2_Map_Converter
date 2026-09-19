# 版本化视觉预设

0.4.0 内置 `c5m1-daylight-v1` 与 `c4m3-overcast-static-v1`，后者提取 C4M3 初始化时的固定阴天参数。两者均不需要目标地图 BSP/LMP。C2→C4 首轮游戏采样、反射导入与原名实机验收已完成；C5 已有接受结果不变。完整游戏资源、官方工具和实际引擎反射采样仍是相应阶段的依赖。

当前未发布版本另登记 `c7m1-hazy-static-v1` 与 `c10m3-night-v1`，采用 schema 3、仅开放 `conversion: generic-replace-*` 通用入口，推荐 v3；不需要参考地图 BSP 作为每次转换的输入。schema 3 支持空的额外方向光、受限太阳材质路径以及按玩家角色区分的曝光/Bloom，详情见 [C7M1](c7m1-preset.zh-CN.md) 与 [C10M3](c10m3-preset.zh-CN.md) 说明。C7M1 已完成 C2M1/C4M3 输入人工验收，C10M3 已完成 C2M1/C5M1 输入人工验收。

下表是**历史 source_profile 入口**的兼容约束；当前通用入口按实际地图能力检查，不按该表限制来源地图。通用入口已有 C7M1→C4M3、C5M1→C4M3 等实际接受结果，不需要给每张新图新增来源适配器。`supported_sources` 仅描述旧适配器兼容性，C7/C10 的空数组不会限制通用输入。四份内置 JSON 均登记在源码 ZIP 与 Python 包的显式资源清单中。

## 配置与版本身份

在完整示例配置中使用以下选择项，其余来源文件和本机路径见 [中文指南](guide.zh-CN.md)：

```json
{
  "source_profile": "c6m1_riverbank",
  "preset": "c5m1-daylight-v1",
  "atmosphere_policy": "replace"
}
```

| 来源 | 目标 | 天气策略 |
|---|---|---|
| `c6m1_riverbank` | `c5m1-daylight-v1` | 默认 `replace`；可显式选 `preserve` |
| `c2m1_highway` | `c5m1-daylight-v1` | 已接受的 `preserve`；其他策略拒绝 |
| `c2m1_highway` | `c4m3-overcast-static-v1` | `replace`，固定阴天并覆盖局部氛围；不支持 `preserve` |
| `c6m1_riverbank` | `c4m3-overcast-static-v1` | 尚不支持；不能由两个已支持选项任意组合 |

此代码块只展示选择项，不是可独立构建的完整配置。`examples/c6-c5.example.json`、`examples/c2-c5.example.json` 和 `examples/c2-c4.example.json` 提供完整模板。C4 配置必须使用原始 C2 输入与新输出目录，详见 [C4 指南](c4m3-guide.zh-CN.md)。

旧配置继续支持 `profile: c6-c5/c2-c5` 加显式 `reference_bsp`、`reference_lmp`。新旧选择项不能混用，缺少一半选择项也会拒绝。已有运行保持原配置和参考文件，后续命令按原清单校验；升级源码不改写旧 `run.json`。转换配置时创建新配置及新输出目录，不覆盖旧配置。

预设保存在 `l4d2_bsp/preset_data/<预设ID>.json`，两个 JSON 均在源码 ZIP、wheel 和 sdist 的明确资源清单中。预设 ID、schema/内容版本和文件 SHA256 标识本次使用的数据，运行清单记录并核验这一身份。`v1` 是固定内容版本；改变参数应产生新版本，而不是覆盖旧运行所用文件。C5 保持 schema 1 及原 JSON 字节；C4 使用 schema 2，schema 版本与预设内容版本分别记录。当前加载器只接受已登记内置预设，不提供任意外部 JSON 导入。

## 数据内容与依赖

预设按视觉角色保存光照、天空、雾、后处理、调色、太阳与色调映射参数，以及采样曝光、资源路径、晴天气氛和目标环境音引用。基础实体和模式补丁通过各自角色视图取得值；只为实际存在差异的数据保留变体。

它不包含目标地图几何、来源位置、模型布置、玩法事件、BSP/LMP/NAV、纹理或音频。资源名称用于在使用者的游戏安装中查找资产，不会把游戏素材嵌入 Python 包。`replace` 中原 C6 天气实体及混合事件分支的归属判断仍属于来源适配器；预设仅规定目标效果和资源。

C4 schema 2 显式支持无 `env_sun`、未设置 bright-pixels、普通风、完整局部后处理与调色角色，以及独立干燥 soundscape 定义。基础雾 end 为 1500，曝光 max/min/rate 为 `10`/`1`/`.25`；不采用闪电瞬间的 max 50。名称含 `storm` 的默认雾或后处理仍可贡献静态基础参数，不导入动态风暴控制器。室外剔除天气后有意不加入环境循环声，室内使用 C4 非天气子树的 room-tone；车辆警报、发电机等来源事件音保留。

`soundscapes.py` 为 C4 生成地图专属 `scripts/soundscapes_<地图名>.txt`，其中仅含所选资源引用的自有定义，不拷贝 Valve 音频或整套关卡脚本。离线/最终包使用原地图名，临时采样包含对应别名脚本。提供 exclude 时，C4 包共有八项文件，比 C5 七文件包多此声音脚本；未提供 exclude 时各少一项。

离线 VRAD 重算世界及静态模型光照，保留旧反射。要取得新场景 HDR cubemap，仍须用户启动完整游戏、按 guard 交接采样并通过导入审计。C5 参考文件的省略不会让工具变成无游戏依赖的转换器。

## 程序接口与扩展边界

`load_preset(预设ID)` 返回 `StylePreset`，提供 `.id`、`.sha256`、`.source_path`、`.metadata()`、`.entities(kind)`、`.capture_exposure_max`、`.required_resources(atmosphere_policy)` 和 `.soundscape_mapping`。schema 2 通过角色、曝光、风及声音定义接口表达可选字段，采样 guard 读取本次预设的曝光。角色实体视图由严格校验的数据建立，不制造 donor BSP。现有转换函数仍可使用旧参考字节，以便维护历史配置和进行结果对照。

| 层 | 负责的变化 | 新增能力时需要的证据 |
|---|---|---|
| 来源适配器：`style.py`、`profiles.py`、`weather.py` | 地图身份、角色定位、局部覆盖、天气所有权、原玩法保护 | 新来源基础及 h/l/s 数据检查、拒绝路径、保护与幂等性、原名实机测试 |
| 目标预设：`presets.py`、`preset_data/` | 角色参数、资源引用、目标曝光和气氛 | schema 校验、资源检查、固定版本与内容哈希、适配器兼容和效果验证 |
| 执行工作流：`workflow.py` 及原生/采样模块 | 输入快照、烘焙、结构审计、游戏采样交接与打包 | 原生工具结果、产物/输入哈希、反射来源审计与包解出核对 |

若使用历史 `source_profile` 路径，增加新来源仍须另写适配器；新地图应优先使用通用入口。新增目标预设不能只加名字：仍须校验角色、资源、采样身份、包分发及实机效果；新增动态天气还可能需要新的范围推导和实体支持。当前没有新增雨区、任意脚本解释、室内外空间语义识别或 GUI。

0.3.0 已将预设与重构前 donor 路径应用到相同 C2/C6 基础和三模式输入，共 8 组结果逐字节相同，重复应用不变。这些检查不等于新烘焙或新实机采样。已有接受结果及本版证据见 [验证说明](validation.md)。

每次新配置构建保存 `<run>/preset.json` 并记录其哈希；采样曝光也从这份快照读取。继续运行时，原安装中的预设文件和快照均须通过清单校验。快照用于审计复现，当前不支持仅携带快照迁移旧运行到其他安装。

`StylePreset.tonemap_inputs(classname)` 返回该玩家角色的受限初始化输入列表；schema 1/2 继续返回既有输出顺序和值，schema 3 才表达角色差异与 Bloom。`exposure_values` 对 schema 3 返回幸存者视图，`capture_exposure_max` 始终对应幸存者采样控制器。null rate/bright_pixels 表示目标没有显式设置，不自动填入其他预设的数值。
