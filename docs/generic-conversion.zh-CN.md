# 通用预设转换（阶段 B）

`conversion: generic-replace-v1` 接入不按地图名分支的强预设转换。它支持按实际输入发现模式补丁，并按实体类型制定覆盖方案；不代表所有官方图或三方图已经通过测试。旧 `source_profile` 配置继续使用原适配路径，不会自动迁移。

## 配置和执行

复制 `examples/generic.example.json` 到新的本地配置，修改所有路径。`preset` 可选 `c5m1-daylight-v1`（C5M1）或 `c4m3-overcast-static-v1`（C4M3 固定阴天）。需要完整游戏资源、Python 3.11+ 和本机工具或独立原生工具包。

```powershell
Copy-Item examples/generic.example.json config.generic.local.json
notepad config.generic.local.json
python -m l4d2_bsp.workflow check --config config.generic.local.json
python -m l4d2_bsp.workflow build --config config.generic.local.json
```

每次使用新的 `output_dir`。`check` 只验证输入、资源和确定性修改，不运行 VRAD。先阅读 JSON 中的方案和 `coverage_warnings`，再构建；错误需解决，不能通过改清单绕过。check 输出（运行清单的 `preflight`）中的 `base_style.plan` / `mode_styles.*.plan` 记录输入哈希、目标预设、字段修改、直接视觉输出删除、实体增删和采样曝光控制器。

| 配置 | 作用 |
|---|---|
| `conversion` | 选择 `generic-replace-v1`、`generic-replace-v2` 或 `generic-replace-v3`；不与 `source_profile`、旧 `profile`、参考地图或手填 `mode_lmps` 混用 |
| `source_bsp` | 原始松散 BSP，不应是本工具已经转换的结果 |
| `preset` | 固定目标参数；无需目标地图 BSP/LMP |
| `game_dir` / `tools_dir` | 完整测试游戏的 `left4dead2` 和工具包的 `bin`；工具包需独立解压 |
| `output_dir` | 尚不存在的本轮输出目录，使用 ASCII 路径 |
| `resource_roots` | 可选；默认按 update、DLC、基础游戏优先级查询实际存在的资源目录 |
| `search_dirs` | 可选伴随文件搜索目录，按列表顺序优先，随后查找源 BSP 所在目录；默认由资源根的 maps 目录推导 |
| `nav` / `exclude` | 可选显式文件；否则自动发现。NAV 必需，不生成导航；exclude 可缺省 |
| `native_mounts` | 示例显式设为 `resource_roots`，向本次编译沙箱写入明确资源挂载；独立工具包应使用该设置。省略仍按兼容默认值 `gameinfo` 处理 |
| `threads` / `timeout_seconds` | VRAD 线程和超时；示例使用 4 / 3600 |

目前处理实际存在的 `h_0`、`l_0`、`s_0` 实体补丁，不要求三份齐全。未知模式、非零索引及未解释的伴随命名会拒绝。输入及发现集合在后续阶段复查；改变输入后要建立新运行。三方 VPK 需先自行解出地图和所需配套资源，当前不提供自动安装三方内容的功能。

## v1 覆盖规则

- 天空、环境光与方向、阴影、远景雾、局部雾控制器、后处理和调色按预设覆盖。保留 sky_camera 的位置和比例、区域体积以及普通局部灯光和材质。
- 将既有点调色控制器启用、权重设为 1 并取消局部衰减；停用区域调色效果，必要时增加全局点调色控制器。指定唯一主控制器。预设未提供且规则未明确规范化的字段保留。
- 曝光控制器均使用目标预设，通过新增初始化实体写入。采样控制器从本轮方案取得，不要求来源地图使用 `tonemap_global`。
- 删除能明确处理的降水及降水阻挡实体，保留其原 brush 数据。C5M1 当前无风参数，因此本规则移除 env_wind；C4M3 按预设风参数覆盖。按预设决定太阳实体的创建、修改或删除。没有新增降雨体积、闪电或随机风暴。
- 仅切断直接写向视觉实体的已识别输入；保留上游共享控制器、无关输出和玩法实体。名字大小写、同名目标和星号候选均参加检查。视觉/玩法混合目标、带脚本的视觉控制器、未解释输入、关联输出或生命周期依赖会拒绝转换。
- 环境音区域统一使用预设的 `outdoor` 声音定义，保留发声点位置和半径。本轮不推断室内外声学分区；这与旧地图适配中的分区选曲有意不同。
- 普通粒子、ambient_generic 事件音和外部脚本保留并报告覆盖缺口。因此不能保证任意地图的脚本天气、粒子雨或雷声音效已全部移除，不能仅凭构建通过宣布晴天完整验收。

实体修改阶段逐项核验方案，未修改实体字节、未授权输出和非实体 lump 均受保护。后续 VRAD 仍使用既有光照审计；反射阶段仍检查原始资源、采样坐标和 HDR 数据。结构保护不等于脚本执行和完整战役已验证。

## 反射采样及人工复核

构建成功后，配置已有的正常启动器，并完全退出游戏：

```powershell
python -m l4d2_bsp.workflow auto-capture --run ./runs/c1-c5-generic-01 --launcher launcher.local.json
python -m l4d2_bsp.workflow status --run ./runs/c1-c5-generic-01
```

将 `--run` 换成本次真实输出目录。首次启动配置及恢复见 [自动采样指南](auto-capture.zh-CN.md)。不适合自动启动的机器可继续使用 [使用指南第 3、4 节](guide.zh-CN.md) 的手动路径，执行本次生成的 CFG，不借用其他轮的 CFG 或反射数据。

状态 `final_ready_pending_user_validation` 只表示工具检查完成。按 [使用指南第 5 节](guide.zh-CN.md) 安装本次原名 VPK，停用同图冲突包并从正常启动器完整重启。控制台的 `map` 后填写本次真实地图名。例如 C1M1：

```text
sv_cheats 1
mat_hdr_level 2
mat_specular 1
map c1m1_hotel coop
```

人工确认天空、远近雾、建筑和模型光照、车辆与手持武器反射、室内外过渡无异常；再验证开场、触发事件和地图路线。记录预设、运行 ID、模式、走过的路线和观察结果。机器参数与人工无异常/不劣化共同构成验收；未测试的地图和模式继续标为未验证。

## 阶段验证范围

本阶段新增规则及旧路径回归合计 325 项：322 通过，3 项因 Windows 符号链接权限跳过。另对 C1M1→C5M1 基础图及实际 l_0、C3M1→C4M3 基础图及实际 h_0/l_0/s_0 执行真实数据转换与保护检查。C1M1 完成使用八文件原生工具包的离线构建、光照审计和 VPK 解包校验。C3M1 随后完成独立工具包烘焙和 44 份 HDR 反射自动采样，最终包已获用户 coop 实测正常反馈；C1M1 用户已反馈本轮最终包 coop 实测全程无异常，验收限实际观察范围，不继承旧地图的验收结果。

独立源码目录及在其中重新解压的原生工具包已完成 C1M1→C5M1 全链：烘焙 → 自动启动 → 84 个 HDR cubemap → 原名回填 → VPK 解包校验 → 临时资源清理。运行 `addbcd67c50046a6` 的状态为 `final_ready_pending_user_validation`，人工结果另行归档：用户反馈整个测试过程无异常；其他模式和未单列的事件覆盖不扩大。独立目录的转换代码与工作区逐文件一致；补齐归档测试夹具和文档不改变该运行的转换代码。

第二张完整机器验证为 C3M1→C4M3（运行 `4041e2b3a56f4baf`）：三份实际模式补丁、烘焙、自动采样与打包清理均通过，最终原名包已获用户 coop 实测正常反馈，验收限实际观察范围。仍未增加雨、雷电，且不声明其他模式已实测。详细证据见 [验证记录](validation.md)。

## v2 天气覆盖扩展

复制 `examples/generic-v2.example.json`，显式选择 `generic-replace-v2`。预设仍为相同版本的 C5M1/C4M3；改变的是来源处理规则。旧配置不会自动升级，v1 的实体输出和审计语义保留。执行 check/build/auto-capture 的步骤相同，每次建立新的运行目录。

- 增加按实体类别限定的雾插值、降水透明度输入处理，不放宽任意 IO。
- 原有风实体按目标预设归一化；C5M1 的风速、阵风速度全部归零。保留其模板和原生命周期，因此模板重建出来的风也使用预设值。
- 使用版本化资源目录 `weather-assets-v1` 与实际事件关系识别天气音效：确定雷声资源为 `Weather.Thunder_close_all_4`；风雨循环声 `Hospital.HelicopterWindLoop`、`ambient/wind/windgust_strong.wav` 及混音层 `stormLayer`、`voipLayer` 只有在所有有效写入均来自已识别天气事件时才处理。同实体的不同事件分别分析，混合调用保留。实体名、地图名、Hammer ID 和坐标不作为天气归属依据。
- 保留上游触发器、共享 relay 和玩法输出，只切断可证明的天气效果端点写入。仅当 relay 的所有 Trigger 调用均属天气事件时传播证据；脚本不参与推断。
- 天气事件中的纯画面震动可以切断，物理/绳索震动保留。会被运行时改写的震动拒绝转换；共享震动的幅度、频率和停止操作保留，避免影响玩法调用。
- 仅识别单一 `r_skyboxfogfactor` 数值命令；其他命令、命令串和未知脚本保留。

方案的 `weather_evidence` 记录资源归属、事件关系、未解析输出和限制。输入音效/脚本、模板或父子关系存在歧义时，可能保留并报告，或拒绝转换；构建成功不代表任意脚本天气均已消除。普通粒子和目录外事件声音继续保留。没有新增降雨体积、雷电或随机天气。

C6M1 是此规则扩展的验证输入，并非按其实体名添加的专用分支。基础图及 h/l/s 补丁已通过方案检查；最终游戏验收结果另记于验证记录，不继承旧 C6 专用适配的验收。尤其需复核新娘 Witch 的尸潮流程，以及原天气触发后的晴天是否保持。

C6M1 v2 最终包已获用户实际观察范围内的接受反馈，但带有已知画质限制 VIS-001：婚礼区域部分白椅过亮，Phong 开关对照未明显改善，根因未确定、按用户要求暂缓。不得将这一结论表述为全图无画质问题或全部模式/事件已验收；详情见 [验证记录](validation.md)。

## v3：无风暴预设去除已知闪电

需要去除原图已知闪电时，复制 `examples/generic-v3.example.json`，选择 `generic-replace-v3`，填写实际输入及全新的输出目录。执行相同的 check → build → auto-capture → 人工验收流程。v1/v2 继续保持原规则，不会自动升级；既有运行不能通过修改清单切换规则版本。

当前支持的 C5M1 晴天和 C4M3 静态阴天都是不含风暴的目标。v3 继承 v2，并使用 `weather-assets-v2` 精确识别 `info_particle_system.effect_name` 中的 `storm_cloud_parent`、`storm_lightning_02`、`storm_lightning_screenglow`。这些是效果资源标识，匹配不依赖地图名、实体名、Hammer ID 或坐标。自启动闪光粒子也会移除；指向已识别端点的 Start/Stop 等已知生命周期输出一并处理，避免后续重新启动。

保留上游 timer、case、relay、director 和它们的无关输出，不因为控制链连接了闪电就删除整条链。未知粒子、脚本控制和资源目录之外的闪电不自动猜测；覆盖限制仍写入方案。已识别粒子若有脚本、输出、全局状态、被模板/父子关系引用、被保留粒子用作控制点，或调用同时命中保留对象，则拒绝转换并报告，交由后续规则迭代。两个都将删除的粒子之间的控制点引用不阻止转换。

v3 不改变局部灯光、材质高光参数、湿地表材质或几何；“暴雨改晴天”并不等于自动把所有物体换成干燥材质。未来若增加真正的暴雨目标，必须另行定义目标天气策略及规则版本，不能直接沿用本节的无风暴契约。
