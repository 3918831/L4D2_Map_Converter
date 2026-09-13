# 源码工具包与仓库准备

本版交付可解压运行的源码工具包。需要使用者另行安装 Python 3.11+，并指定自己的完整 L4D2 游戏资源和官方工具。它不是包含运行时的单文件 EXE，不提供 GUI 或游戏启动器程序；可通过配置调用本机已有启动器进行无注入自动采样。

## 内容与依赖

| 包含 | 不包含 |
|---|---|
| 自有 Python 模块、必要脚本、JSON 示例、版本化视觉预设 JSON | Python 解释器和第三方运行环境 |
| 合成测试、指南、架构、精选验证结论 | Valve 地图、NAV/LMP、材质、模型、反射纹理 |
| 元数据、忽略规则、归档脚本 | 官方编译器、游戏程序、私人启动器 |
| 版本变更说明 | 本机配置、原始日志、缓存、历史实验包、账户信息 |

官方文件由使用者本机提供，不作为测试 fixture 或源码附件再分发。历史实验与大文件备份独立于源码 ZIP 保存。

`c5m1-daylight-v1` 包含目标角色参数和资源名称，不含 C5 BSP/LMP 或材质、声音等资源内容。它随源码 ZIP 和 Python wheel/sdist 作为包数据分发；实际转换无需提供 C5 donor 文件，仍需来源地图、完整游戏资产和官方工具。预设 ID、版本和内容 SHA256 可在运行记录中核验。

## 解压验证

将 ZIP 解压到新的 ASCII 路径，在该目录打开 PowerShell：

```powershell
python --version
python -m unittest discover -s tests -v
python -m l4d2_bsp.workflow --help
python -c "from l4d2_bsp.presets import load_preset; p = load_preset('c5m1-daylight-v1'); print(p.id, p.sha256)"
```

这些检查不要求游戏资产。实际转换另需复制示例配置、修改全部路径并运行 `check`，详见 [中文指南](guide.zh-CN.md)。每次地图包必须依据自己的 `run.json`、原生审计和实机结果判断，不能仅凭源码 ZIP 校验通过就认定视觉通过。

维护者生成源码包时使用项目提供的显式文件清单归档脚本，不直接压缩整个工作目录。归档后在独立目录运行测试和 CLI 帮助，检查条目没有游戏资产、个人绝对路径或运行日志。工具包自身校验与本机集成/实机结果分别报告。

```powershell
python scripts/make_release.py --output artifacts/release/l4d2-map-converter-source.zip
```

目标 ZIP 必须不存在。脚本同时生成 ZIP 的 `.sha256` 文件，包内 `FILES.sha256` 记录源码条目哈希；它们用于识别收到的具体源码版本。

## 公开状态与许可证

源码仓库为 [3918831/L4D2_Map_Converter](https://github.com/3918831/L4D2_Map_Converter)，本版归档标签为 `v0.5.0`。本地版本是否已同步，以远端提交和标签记录为准。项目许可证尚未选择；本版不擅自添加 MIT 或其他许可，也不代表已取得再发行 Valve 内容的授权。公开发布时由项目所有者确定源码许可、第三方说明和最终上传内容。

本机配置、清单和日志可能含个人路径，留在本机。公开问题可附脱敏错误及必要截图；完整 BSP、VPK、NAV、LMP 和官方工具不进入源码仓库。

## 本版状态

0.5.0 保留内置 `c5m1-daylight-v1` 和 `c4m3-overcast-static-v1`、来源适配及显式 donor 旧配置；新增可选 `auto-capture` 与 STOP 恢复命令，详见 [自动采样指南](auto-capture.zh-CN.md)。C6 晴天及 C2→C4 固定阴天此前已获用户反馈接受，具体产物哈希和未覆盖范围见 [验证说明](validation.md)。新生成包仍独立验收；旧配置、预设和成果不自动迁移。任意外部预设导入、新增雨区、GUI/EXE、其他地图及 S0—S7 八阶段演示仍未实现。
