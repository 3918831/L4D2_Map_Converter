# L4D2 Map Converter

在原 BSP 的副本上迁移 C5 全局视觉风格，使用官方 L4D2 VRAD 重烘焙 HDR 世界与静态模型光照，并检查地图结构及玩法数据的保护范围。无需 VMF、反编译、VBSP 或 VVIS。

这是 **Windows / Python 3.11+ 的源码工具包**，Python 部分只使用标准库。游戏、地图资源和官方 `vrad.exe`、`bspzip.exe`、`vpk.exe` 由使用者的本机安装提供；发行包不包含 Python 运行环境、Valve 工具或游戏素材。

## 当前范围

| 配置 | 来源 → 参考 | 验证范围 |
|---|---|---|
| `c2-c5` | C2M1 Highway → C5M1 Waterfront | 历史 18 号 HDR 成果已获用户验收；新工作流每次构建仍需独立检查 |
| `c6-c5` | C6M1 Riverbank → C5M1 Waterfront | 基础 BSP 和三模式 LMP 静态检查通过；实机画面、反射捕获和玩法验收待用户独立测试 |
| 其他官方图、自定义图、其他参考天气 | — | 尚不支持 |

C6 配置迁移全局色彩、天空和光照等字段，**保留原有风暴、雨和闪电/曝光闪烁事件**，不等于将整张图变成完全无雨的 C5 晴天。结构审计通过也不等于完整战役流程通过。

## 两种输出

- **离线包**：全质量 HDR 四跳反弹，静态模型光照、模型几何遮挡和纹理阴影；保留原反射纹理。构建不部署、不启动游戏。
- **完成反射的包**：用户启动游戏并执行生成的采样命令；工具接收、审计原生 HDR cubemap 后回填原地图名并打包。LDR 和默认反射保留，最终画面仍由用户验收。

截图用于观察和比较，不是转换算法的输入。反射采样由游戏渲染，不能用截图或 VRAD 光照结果替代。

## 开始

解压源码后，在项目根目录打开 PowerShell。工具包和输出目录使用 ASCII 路径，并放在游戏安装目录之外。

```powershell
python --version
python -m unittest discover -s tests -v
Copy-Item -LiteralPath examples/c6-c5.example.json -Destination config.local.json
notepad config.local.json
```

修改模板中的所有本机路径，然后检查和构建：

```powershell
python -m l4d2_bsp.workflow check --config config.local.json
python -m l4d2_bsp.workflow build --config config.local.json
```

完整步骤，包括 C6 输入位置、离线包安装、游戏采样、故障处理和回退，见 [中文使用指南](docs/guide.zh-CN.md)。第一次 C6 测试前请读完采样交接部分；它包含必须等待开场、材质重载和原生采样完成的步骤。

| 文档 | 内容 |
|---|---|
| [使用指南](docs/guide.zh-CN.md) | 可复制命令、配置、人工步骤、回退、固定机位 |
| [架构与保护合同](docs/architecture.md) | 原理、模块分工、审计边界 |
| [验证与证据](docs/validation.md) | 已确认范围、历史基线、用户验收记录 |
| [源码发行说明](docs/release.md) | 依赖、归档范围、仓库与许可证状态 |
| [变更记录](CHANGELOG.md) | 当前能力和后续工作 |

每次运行将配置、输入哈希、阶段状态、日志及产物记录到输出目录的 `run.json`。构建后不要修改本次配置、输入或已记录的产物；失败烘焙需保留证据并使用新的输出目录重新构建。

当前未提供 GUI、独立 EXE、无人值守游戏启动或 S0—S7 八阶段演示生成器。可以对原版、离线包、完成反射的包进行同机位截图比较。许可证和远程仓库由项目所有者另行选择；此工具包没有授予 Valve 资源的再发行许可。
