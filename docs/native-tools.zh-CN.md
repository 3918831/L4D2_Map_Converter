# 独立 Windows 原生工具包

转换器支持把原生工具独立解压，再通过现有 `tools_dir` 使用。使用者因此不必另外安装 Authoring Tools；仍须安装完整且兼容的 L4D2 游戏，提供地图、模型、材质和正常启动器。Python 3.11+ 仍是源码转换器的依赖，最终 HDR 反射仍由游戏实际渲染生成。

此功能从转换器 **0.6.0** 开始提供。原生二进制包使用独立版本 `v1`；它与转换器源码版本分别标识，不能用旧版 0.5.0 源码替代配套的 0.6.0。

## 使用

将单独的 `l4d2-native-tools-windows-x86-v1.zip` 解压到 ASCII 路径，例如 `E:/L4D2NativeTools/v1`。保持 `bin` 内文件在同一个目录，在**新运行的配置**中设置：

```json
{
  "tools_dir": "E:/L4D2NativeTools/v1/bin",
  "native_mounts": "resource_roots",
  "game_dir": "E:/Games/Left 4 Dead 2/left4dead2",
  "output_dir": "E:/L4D2Runs/c6-c5-001"
}
```

这是配置片段，须合入完整的来源地图配置。独立工具包必须同时设置 `native_mounts: resource_roots`，让 VRAD 按配置中的资源目录顺序读取完整游戏及 DLC。省略 `resource_roots` 时使用转换器的默认 update、DLC、基础目录顺序；自定义时由使用者明确列出完整有序路径。输出目录必须在游戏目录和 `tools_dir` 的父目录之外；不要把输出放进工具包目录。之后按原指南执行：

```powershell
python -m l4d2_bsp.workflow check --config config.local.json
python -m l4d2_bsp.workflow build --config config.local.json
```

转换器在本次运行目录生成只供烘焙使用的 `gameinfo.txt`，用绝对路径挂载资源，并记录其 SHA256；不会修改真实游戏的 `gameinfo.txt`。后续人工或自动采样仍使用真实 `game_dir`。已有运行记录工具和配置的哈希；不要通过更改旧配置来迁移工具目录，应创建新配置与新输出目录。

未设置 `native_mounts` 的旧配置默认仍为 `gameinfo`，保持原安装工具的行为。原版 `gameinfo.txt` 中普通相对 SearchPaths 会以原生工具所在位置推导基目录；单纯移动 EXE/DLL、只指定 `-game` 无法保证 DLC 挂载正确。此机制可参考 [Valve 文件系统初始化源码](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/filesystem_init.cpp)，本次也通过 C6 缺失模型与绝对路径挂载的对照测试核验。SDK 源码仅解释机制，实际兼容性以本次 L4D2 二进制测试为准。

## 包含什么

| 文件 | 作用 |
|---|---|
| `vrad.exe`、`vrad_dll.dll` | 光照烘焙启动程序与实现 |
| `bspzip.exe` | BSP 内资源回填 |
| `vpk.exe` | 地图包创建与解包验证 |
| `tier0.dll`、`vstdlib.dll` | 原生工具共用运行库 |
| `filesystem_stdio.dll` | 游戏资源文件系统，包括运行时加载 |
| `vphysics.dll` | 烘焙使用的模型/物理相关支持 |

这八个文件来自同一份本机官方工具安装，按原字节打包。本次未压缩二进制合计 5,034,432 字节，ZIP 为 2,320,113 字节（约 2.21 MiB）。包中另有 `MANIFEST.json`、二进制逐文件 `FILES.sha256` 和简要说明；ZIP 外部校验值见随附 `.sha256` 文件。清单用于身份与完整性核对，不是数字签名或对来源真实性的认证。

工具是 x86 PE32 程序，本次在 Windows 11 的 64 位环境运行。没有附带 Windows 系统 DLL、游戏运行程序、地图、模型、材质、Python 或启动器。尚未验证其他 Windows 版本和全新机器；不提供 Linux 支持。不能把其他 Source 分支的同名 DLL 混入本包。

资源挂载路径须为 ASCII，含中文等非 ASCII 路径会在检查阶段拒绝。日志可能保留 `Can't find steam.dll relative to executable path` 提示；本次便携工具的完整成功测试没有加载 `steam.dll`，因此没有为消除此提示而添加它。是否成功仍以原生退出码、模型/材质诊断、结构与打包审计为准。

## 维护者重新打包

从同一份兼容的官方工具目录读取文件，生成一个不存在的新 ZIP：

```powershell
python scripts/make_tool_bundle.py --tools-dir "E:/SteamLibrary/steamapps/common/Left 4 Dead 2/bin" --output "artifacts/release/l4d2-native-tools-windows-x86-v1.zip"
Get-FileHash -Algorithm SHA256 -LiteralPath "artifacts/release/l4d2-native-tools-windows-x86-v1.zip"
```

脚本固定八文件清单，检查 PE 架构、输入稳定性、ZIP CRC 和解包后的逐字节一致性，拒绝已有输出及链接路径。它不自动下载 DLL，也不证明任意同名 x86 文件兼容；更换原工具版本后需要重新做原生集成测试。命令输出 ZIP 的 SHA256；包内清单记录每个二进制的 SHA256。

源码 ZIP 与二进制 ZIP 单独生成。`make_release.py` 包含打包脚本和本指南，继续排除所有 EXE/DLL 及本机运行资料。二进制不进入 Git 源码历史。文件权利归原权利人，适用许可继续有效，本项目不额外授予 Valve 文件权利。

## 验证范围

本次检查了 PE 导入依赖，并在仅保留 Windows 系统目录的 `PATH` 下运行原生工具。C2 完整 HDR 四跳烘焙及结构审计通过；DLL 加载事件追踪中，所有非系统模块均来自独立工具目录。

C2/C6 的历史 HDR 采样分别以 63/48 份反射进行回填回归测试；BSPZIP 审计、VPK 打包和原生解包逐字节检查通过。ZIP 解压到另一目录后重复上述测试，最终 VPK 与对应原工具产物逐字节一致。这里复用了历史采样以验证工具替换，没有将历史采样导入新烘焙地图，也未把它算作新游戏画面验收。

C6 使用普通相对挂载时，在带跟踪和不带跟踪两次测试中均因缺失 DLC 模型失败；原安装工具对照正常退出。明确挂载资源后的便携 VRAD 完成了全质量烘焙及结构审计，没有缺失模型/材质诊断，加载的非系统 DLL 均在独立包内。C2 通过实际 `workflow build` 完成八文件离线包与原生解包检查，状态为 `offline_ready`。本轮源码测试共 260 项，257 项通过、3 项因 Windows 符号链接权限限制跳过。测试日志、模块路径和失败样本保留在本机证据目录，不进入源码包；这些新烘焙产物尚未做游戏内视觉验收。

这次 C6 复测还发现，原安装工具和便携工具都会重算 49 个叶节点的 3D 天空可见标记。`resource_roots` 模式对叶节点 v1 的天空标记采用字段级审计，只接受 `LEAF_FLAGS_SKY` / `LEAF_FLAGS_SKY2D` 的变化；叶数量、区域、包围盒、碰撞内容、cluster、radial 标记和所有其他位仍逐字节保护。原始 PVS 数据仍不允许改变。字段定义见 [bspfile.h](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/public/bspfile.h)，VRAD 的重算逻辑见 [BuildVisForLightEnvironment](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/utils/vrad/lightmap.cpp)。这属于天空渲染标记的有限支持，并非放开空间结构审计；旧配置继续保持原来的完整叶数据保护。
