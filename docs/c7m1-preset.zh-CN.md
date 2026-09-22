# C7M1 常态薄雾预设

预设 ID：`c7m1-hazy-static-v1`，schema 3。原始 C2M1 和 C4M3 输入均已完成机器流程并获用户画面验收，用户确认本预设当前阶段可以验收。详见 [验证记录](validation.md)。不将已测样本通过扩大为任意地图或全部模式通过，也不称为完整复制 C7M1 场景。

## 提取什么状态

选择原始 C7M1 的默认室外控制器及初始化曝光，形成暖色直射光、冷色环境光、蓝灰雾、river 天空和 docks 调色的固定组合。参考基础 BSP 与当前安装的 h/l/s 模式补丁分别检查，24 个氛围角色实体的参数一致（排除身份/位置字段），相关输出也一致。不需要为不同模式杜撰另一份目标参数。

| 参数 | 固定值／策略 |
|---|---|
| 天空 | `river_hdr` |
| 主光 | `185 157 115 60`，yaw 18、pitch -15、SunSpreadAngle 1 |
| 环境光 | `133 152 167 15`；HDR 使用原回退标记与倍率 1 |
| 额外方向光 | 参考图没有 `light_directional`，预设显式为 null；移除输入图中该类额外控制器，沿用输出、父子及模板引用保护 |
| 阴影 | RGB `92 92 92`，angles `50 18 0`，distance 70；允许局部灯光阴影 |
| 默认世界雾 | RGB `33 43 50`，start 256、end 2500、density 1、farz 2500、lerp 4 |
| 天空雾 | 同色，start 128、end 2000、density 1；不复制参考图 sky_camera 的位置、旋转或 scale |
| 默认调色 | `materials/correction/docks.raw`；室内角色另记录 `docks_interiors.raw` |
| 后处理 | exterior：vignette .8→1.1、localcontrast .5、edges 0、grain 1、fade 2 |
| 幸存者曝光 | min .25、max 9；Bloom scale 0、exponent 2、saturation 1 |
| 感染者／ghost 曝光 | min .25、max 3；Bloom scale 1、exponent 2、saturation 1 |
| 未显式设置的曝光项 | rate、bright_pixels 为 null，不套用 C5M1/C4M3 的数值；不宣称穷举或重置所有引擎默认／外部 cvar |
| 太阳 | `sprites/light_glow02_add_noz_docks`，size 64、overlay 96、RGB 51、HDRColorScale .075，yaw 14、pitch -16 |
| 普通风 | 15–30，gust 50–100，delay 15–30，duration 5，direction `0 -180 0` |

缺少的 height-fog 字段以明确的平面雾策略补齐：density/start 为 0、maxdensity 为 1。这些是预设规范化值，不伪称参考图里存在这些键。`color_intro` 使用常态 docks 调色，不把开局水边局部滤镜当作全图目标。太阳的硬件等级门槛不作为视觉角色迁移，不复制参考图的性能分级或实体身份。

## 通用性与范围

转换仍由实体类别和图关系规划：预设不包含来源地图名条件、Hammer ID、坐标、模型、几何或玩法事件。新增能力是“可空的额外方向光”“按玩家角色定义曝光/Bloom”“受限路径的太阳材质”，不是 C7 专用分支。schema 1/2 的 C5M1/C4M3 原 JSON 不变；schema 3 只接通用配置，`supported_sources: []` 表示不开放旧 source_profile 适配器，**不是输入地图白名单为空**。

沿用强覆盖原则：输入图所有普通雾、调色、后处理区域采用目标常态室外参数，原空间布置保持；不自动识别新地图里的地下室、窑炉或坦克遭遇区。预设保存室内角色以供明确的后续能力使用，当前 generic 入口不会据此自动划分室内。所有 soundscape 区域同样使用目标 outdoor 定义。局部灯光、材质参数默认保留；仍在实验中的材质减反光选项不随本预设自动开启。

不导入 C7M1 的坦克事件高反差后处理、窑炉和列车局部调色、局部雾粒子、燃烧城市、火灾、船只水波、NPC/战斗和音乐事件。这些依赖空间语义或原玩法，盲目复制无法通用。主光与天空可以重现氛围组成，原地图建筑、火光、物件和材质仍会使最终视觉不同。参考图脚本只记录入口，未进行任意脚本语义执行或证明所有运行时写入不存在。

现有目标是固定、无新增风暴的氛围。v3 继续覆盖已识别的来源风暴／闪电；未知来源粒子和事件音保持并报告。不新增雨区、随机雷电或 C7 场景粒子。

## 声音和资源

从 `river_01.spawn → river.shoreline`、`river_01.street → urban.street` 共同的非定位背景提取 `crucial_town_ambience.wav`（volume .8、pitch 100）。室内角色从 `river_01.interiors → urban.apartment` 提取 `crucial_quiet_cellblock_amb.wav`（.65、100）。忽略定点水边、火焰、灯具声以及随机枪炮、爆炸、海鸥、汽笛和风阵；没有复制整套关卡声音脚本。

生成自有 `lmc_c7m1_hazy_v1.*` 定义，封装为本次地图的 `scripts/soundscapes_<map>.txt`，原安装不改写。源图本身的事件声音仍由通用保护规则决定是否保留，不承诺所有输入都没有场景音。

预设直接依赖 6 个天空 VMT、2 个调色 RAW、1 个太阳 VMT 和 2 个底噪 WAV。加载预设不再需要 C7M1 BSP/LMP；运行转换仍需完整游戏资源和打包原生工具，反射仍由引擎采样。资源存在性与嵌套材质纹理检查记录在本机证据中，不等于已证明任意输入地图的全部资源闭包。

## 使用和下一轮验证

复制 `examples/generic-c7m1.example.json`，修改游戏、工具、输入和全新输出目录。新建运行使用 `conversion: generic-replace-v4`（[保留来源声音](source-audio.zh-CN.md)，历史 v3 验收记录不变）、`preset: c7m1-hazy-static-v1`，沿用 check → build → auto-capture → 人工验收。不能将新的目标 ID 塞入旧 c2-c5/c6-c5 配置。

采样 guard 按幸存者控制器期待 max 9，不会误用感染者的 3。建议下一轮选择已验证的原始 C2M1 或 C5M1，覆盖室外、室内、阴影和近景反射；人工确认无黑线、缺材质、异常发白及玩法退化。新预设的感染者／ghost 参数已离线验证，但其游戏视角仍须单独实测，不能由合作模式画面代替。

本机提取证据在 `artifacts/c7m1-preset-20260918/`，包括参考文件哈希、角色与模式对照、选择依据、原资源引用和检查结果；不把原 BSP、原关卡声音脚本、RAW、贴图、音频加入源码发布。自动测试与实际离线结果见 [验证记录](validation.md)。
