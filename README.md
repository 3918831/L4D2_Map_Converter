# L4D2 Map Converter

在原 BSP 的副本上应用版本化视觉预设，使用官方 L4D2 VRAD 重烘焙 HDR 世界与静态模型光照，并检查地图结构及玩法数据的保护范围。0.6.0 新增独立 Windows 原生工具包及明确资源挂载，保留 `c5m1-daylight-v1`、`c4m3-overcast-static-v1` 和可选无注入自动 HDR 反射采样；此前 C2→C4 固定阴天已完成用户实机验收。新增通用转换入口详见 [通用转换指南](docs/generic-conversion.zh-CN.md)，旧适配入口保持。内置预设无需目标地图 BSP/LMP；无需 VMF、反编译、VBSP 或 VVIS。

这是 **Windows / Python 3.11+ 的源码工具包**，Python 部分只使用标准库。完整游戏和地图资源由使用者的本机安装提供；原生工具可使用本机 Authoring Tools，或单独解压的 [Windows 原生工具包](docs/native-tools.zh-CN.md)。源码 ZIP 不包含 Python 运行环境、Valve 工具或游戏素材。

未发布版本新增通用入口专用的 [`c7m1-hazy-static-v1`](docs/c7m1-preset.zh-CN.md)：C7M1 常态暖光与蓝灰雾，含角色曝光/Bloom、专用太阳材质及有限环境底噪，C2M1/C4M3 输入均已获用户画面验收。新增 [`c10m3-night-v1`](docs/c10m3-preset.zh-CN.md) 提取 C10M3 蓝青色夜间光照、雾与调色，C2M1 输入已获人工接受，接续测试 C5M1。预设不移植参考图场景事件。材质残留光泽 VIS-003 按用户要求暂缓，默认仍保留材质。

## 当前范围

| `source_profile` | 来源 → `preset` | 验证范围 |
|---|---|---|
| `c2m1_highway` | C2M1 Highway → `c5m1-daylight-v1` | 使用已接受的 `preserve` 策略；历史 18 号 HDR 成果已获用户验收，新构建仍独立检查 |
| `c6m1_riverbank` | C6M1 Riverbank → `c5m1-daylight-v1` | 默认 `replace` 晴天，可选 `preserve`；已完成晴天 HDR 反射导入，用户测试反馈基本无问题。验收限于该包及实际测试范围 |
| `c2m1_highway` | 原始 C2M1 Highway → `c4m3-overcast-static-v1` | 固定阴天，使用 `replace`；63 份 HDR 反射导入及原名实机验收通过，无新增雨、雷电或风暴；验收限本次包与实际观察范围 |
| `conversion: generic-replace-v1` | 任意合法地图名 → C5M1 / C4M3 | 新通用路径；C1M1/C3M1 已通过参数保护检查，C1M1 已在独立目录完成原生构建与 84 份 HDR 反射回填；用户已反馈本轮 coop 实测全程无异常，验收限实际观察范围；C3M1→C4M3 已完成原生构建及 44 份 HDR 回填，用户反馈 coop 实测正常；其他输入按能力检查处理 |

`generic-replace-v2` 进一步按资源和事件关系处理天气端点，C6M1→C5M1 已在实际观察范围内带白椅过亮 VIS-001 接受。[批量工作流](docs/batch-testing.zh-CN.md) 提供只读 `batch-check` 和串行构建/游戏采样 `batch-run`；共享环境异常会停止队列，最终包仍需逐图人工验收。

首个实际批次中，C7M1→C4M3 用户反馈无异常；C4M3→C5M1 在实际观察范围内带无声闪电残留 VIS-002、局部湿润光泽 VIS-003 接受。后续显式 `generic-replace-v3` 通过资源目录移除已知闪电粒子及直接控制输出，用户已确认 C4M3 新包无闪电或闪光。材质默认保留，新增可选 `material_policy: catalogued-static-reflections-v1`，为目录内静态模型生成私有材质副本并减弱反光，实际画质单独验收。详见 [通用指南](docs/generic-conversion.zh-CN.md) 和 [验证记录](docs/validation.md)，不宣称任意输入或材质已覆盖。

C6 默认 **覆盖原暴雨、雷声、风暴/闪电曝光、局部雾与后处理、检查点调色、雨声环境音**。建筑、物件、局部灯光/材质、碰撞、NAV 和无关推进事件保留；例如新娘 Witch 仍触发尸潮，只取消风暴分支。设置 `"atmosphere_policy": "preserve"` 可使用旧行为。当前是受限 C6 适配，并非任意地图识别器；结构审计通过也不等于完整战役流程通过。

预设只包含视觉参数、资源名称、曝光和天气策略，不含地图几何或游戏素材。仍需完整游戏安装、来源地图及官方工具；新 HDR 反射仍由游戏内采样生成。旧 `profile: c6-c5/c2-c5` 加显式 C5 参考路径的配置继续支持，不能与新选择项混用。详见 [预设与扩展边界](docs/presets.md)。

C4 测试从原始 C2 输入建立全新运行，覆盖各局部雾、后处理、调色入口和环境音，保留普通风与局部灯光/材质。C5 预设、旧配置和已接受成果继续保留；测试时仅停用与本次原地图冲突的旧 C5 addon。配置和采样差异见 [C4 固定阴天指南](docs/c4m3-guide.zh-CN.md)。

## 两种输出

- **离线包**：全质量 HDR 四跳反弹，静态模型光照、模型几何遮挡和纹理阴影；保留原反射纹理。构建不部署、不启动游戏。
- **完成反射的包**：自动流程或人工 CFG 驱动游戏采样；工具审计原生 HDR cubemap 后回填原地图名并打包。LDR 和默认反射保留，最终画面仍由用户验收。

截图用于观察和比较，不是转换算法的输入。反射采样由游戏渲染，不能用截图或 VRAD 光照结果替代。

## 开始

解压源码后，在项目根目录打开 PowerShell。工具包和输出目录使用 ASCII 路径，并放在游戏安装目录之外。

```powershell
python --version
python -m unittest discover -s tests -v
if (Test-Path -LiteralPath config.local.json) { throw '配置已存在；按中文指南第 2.2 节迁移，不要覆盖旧运行的配置。' }
Copy-Item -LiteralPath examples/c6-c5.example.json -Destination config.local.json
notepad config.local.json
```

修改模板中的所有本机路径，然后检查和构建：

```powershell
python -m l4d2_bsp.workflow check --config config.local.json
python -m l4d2_bsp.workflow build --config config.local.json
```

完整步骤，包括 C6 输入位置、旧天气运行迁移、离线包安装、游戏采样、故障处理和回退，见 [中文使用指南](docs/guide.zh-CN.md)。采样须用正常启动器带 `-insecure` 完整重启，并核对实际游戏进程参数；原生采样后会重载地图，不能因重连拒绝就重复采样或删除插件。

对 `replace` 路径，配置正常启动器后可以用一条命令完成启动、采样、退出、审计回填和清理：

```powershell
python -m l4d2_bsp.workflow auto-capture --run ./runs/c6-c5-001 --launcher launcher.local.json
```

运行目录换成实际 `output_dir`。首次配置、已验证启动器范围和失败恢复见 [自动采样指南](docs/auto-capture.zh-CN.md)。游戏仍需实际渲染，不依赖 DLL 注入或 `l4d2-portal`。

0.1.0 用户若在 C6 构建中遇到 `VHV topology changed`，请使用当前版本并按指南第 2.1 节重试。修复根据当前模型资源核验旧光照缓存的版本变化，保留原有地图结构保护。从保留天气的旧运行升级到晴天，按第 2.2 节使用新配置/输出重新构建。

| 文档 | 内容 |
|---|---|
| [使用指南](docs/guide.zh-CN.md) | 可复制命令、配置、人工步骤、回退、固定机位 |
| [C4 固定阴天指南](docs/c4m3-guide.zh-CN.md) | C2→C4 配置、固定参数、干燥环境音与验收范围 |
| [自动采样指南](docs/auto-capture.zh-CN.md) | 启动器配置、一条命令采样、失败恢复及人工验收边界 |
| [独立原生工具包](docs/native-tools.zh-CN.md) | 八文件 Windows 工具包、`tools_dir` 配置、重新打包与验证范围 |
| [架构与保护合同](docs/architecture.md) | 原理、模块分工、审计边界 |
| [预设与扩展边界](docs/presets.md) | C5M1/C4M3 预设、schema 兼容、版本身份和来源适配边界 |
| [通用化演进策略](docs/evolution-strategy.zh-CN.md) | 已确认的通用程序与 Agent 分工、当前地图专用限制、后续阶段与验收标准 |
| [通用地图分析（第一阶段）](docs/generic-analysis.zh-CN.md) | 不限定地图名的只读输入/角色/IO 分析 |
| [通用预设转换](docs/generic-conversion.zh-CN.md) | 通用配置、覆盖规则、拒绝条件、采样和人工复核 |
| [验证与证据](docs/validation.md) | 已确认范围、历史基线、用户验收记录 |
| [源码发行说明](docs/release.md) | 依赖、归档范围、仓库与许可证状态 |
| [变更记录](CHANGELOG.md) | 当前能力和后续工作 |

每次运行将配置、输入哈希、阶段状态、日志及产物记录到输出目录的 `run.json`。构建后不要修改本次配置、输入或已记录的产物；失败烘焙需保留证据并使用新的输出目录重新构建。

当前未提供 GUI、独立 EXE 或 S0—S7 八阶段演示生成器。自动采样限已支持的配置与本机启动条件，不保证任意安装都能无人参与完成。可以对原版、离线包、完成反射的包进行同机位截图比较。源码仓库见 [发行说明](docs/release.md)；许可证尚未选择，此工具包没有授予 Valve 资源的再发行许可。
