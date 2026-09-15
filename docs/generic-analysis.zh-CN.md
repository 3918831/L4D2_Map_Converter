# 通用地图分析：第一阶段

本阶段新增只读 `analyze` 命令，不限定 C2/C6 地图名称。它发现松散 BSP 的伴随文件、检查基础与各模式实体、列出视觉角色和潜在 IO 关系，为下一阶段的通用转换提供证据。

**它不生成修改方案或新 BSP，也不代表其他地图已经可以转换。** 原 `check`、`build`、采样和打包的来源限制仍然存在；使用旧配置的行为保持。

后续统一称 **C5M1 预设**，现有标识仍为 `c5m1-daylight-v1`。历史配置、预设文件和旧运行不因此更名。

## 使用

在包含本阶段代码的工程根目录执行。路径替换为自己的安装位置；本命令不需要 VRAD，也不会启动游戏。

```powershell
python -m l4d2_bsp.workflow analyze --bsp "D:/Maps/my_map.bsp" --preset c5m1-daylight-v1
```

如果模式补丁位于 update/maps 等其他目录，可以重复提供 `--search-dir`；按提供顺序搜索，BSP 同目录最后补入。显式 BSP 始终是输入，不会被搜索目录中的同名 BSP 替换。

```powershell
python -m l4d2_bsp.workflow analyze --bsp "D:/L4D2/left4dead2/maps/c1m1_hotel.bsp" --preset c5m1-daylight-v1 --search-dir "D:/L4D2/update/maps" --resource-root "D:/L4D2/update" --resource-root "D:/L4D2/left4dead2_dlc3" --resource-root "D:/L4D2/left4dead2_dlc2" --resource-root "D:/L4D2/left4dead2_dlc1" --resource-root "D:/L4D2/left4dead2"
```

每个提供的目录必须实际存在。也可改用 `--preset c4m3-overcast-static-v1`。分析预设资源不要求这张图已通过来源适配；它不绕过旧构建命令的限制。

结果以 JSON 输出。保存报告时使用新文件名，并指定 UTF-8：

```powershell
if (Test-Path -LiteralPath ./analysis-001.json) { throw '报告已存在，请使用新名称。' }
python -m l4d2_bsp.workflow analyze --bsp "D:/Maps/my_map.bsp" --preset c5m1-daylight-v1 | Out-File -LiteralPath ./analysis-001.json -Encoding utf8
$LASTEXITCODE
```

不要把重定向目标设为输入文件、配置或已有证据。程序本身只读；上述保存动作由 shell 执行。

## 报告解释

| 字段 | 含义 |
|---|---|
| `inputs.source_bsp` | 显式输入的路径、大小和 SHA256 |
| `inputs.mode_lmps` | 实际发现的模式及补丁索引，如 `l_0`；不制造缺失 h/s |
| `selected / candidates` | 选中与被较高优先目录覆盖的候选；顺序是本次搜索约定，不是已验证的游戏挂载 |
| `containers.base / l_0 / …` | 基础 BSP 和各份实际 LMP 各自的检查与实体分析 |
| `atmosphere.roles` | 按类型识别的原始实体序号；缺失角色为空，多实例全部保留 |
| `atmosphere.outputs` | 原始输出及其实体/字段序号、解析值、候选目标和潜在可达氛围标记 |
| `mixed_output_sources` | 同时存在潜在氛围关联和其他输出的实体；不能据此删除任何分支 |
| `script_entrypoints` | VScript/thinkfunction 和已识别脚本调用；尚未解释其语义 |
| `resource_check` | 显式资源根中的预设资源候选；不提供资源根时为 `not_checked` |
| `findings` | 结构错误、脚本待分析、动态目标、版本差异、资源和其他能力缺口 |
| `stages` | 分析状态；转换、烘焙、采样、打包和人工验收均为 `not_run` |

正常完成分析退出 0，可能带 `completed_with_findings`，仍需阅读报告。解析或实体结构失败退出 2，并尽量保留其他容器的 JSON 诊断。文件不存在、目录不可读、参数错误或分析期间输入改变时，退出 2 并输出错误。**退出 0 不表示转换支持或画面验收通过。**

只在声明目录中查找松散文件，不自动解包三方 VPK，也不证明没有其他模式补丁。未知模式字母、非零补丁索引、非标准补丁命名会列出，尚未决定如何用于构建。NAV 未发现时记录缺口，不生成 NAV。

## IO 与资源分析边界

IO 图是候选关系：保留重复输出，支持 ESC 和逗号字段、`OutAnger`、普通目标名、类名、星号候选和 `!self`；动态上下文和未解析目标单列。没有模拟引擎输入分派、事件时序、模板创建、外部脚本或动态生成目标名。

因此，同一个开场或电梯控制器即使能间接到达雾/后处理实体，也不能判定为“天气控制器”。图中的关联只是后续检查线索，`automatic_removal_authorized` 固定为 false。未关联输出也不因此被证明为玩法事件。

预设资源查询复用当前工具的候选查询能力；不验证完整材质依赖闭包、所有 VPK 内容或游戏运行挂载。BSP 内嵌资源单独列在结构清单中，此阶段不将其合并为最终挂载选择。模型缓存和反射布局仍需后续实际构建审计。

## 阶段回归要求

1. 输入发现改造后，运行新输入测试和全部旧测试。
2. 角色/IO 改造后，增加混合事件、脚本、循环、重复输出、动态目标及缺失/多实例测试，再运行完整回归。
3. 接入 CLI 后，对真实非 C2/C6 地图只读检查，确认输入哈希未变及后续阶段没有被执行。
4. 后续接入实际转换时，继续检查旧配置兼容、预设参数、保护数据和原生工具结果；涉及实际效果才增加游戏内验证。

不要求每个只读步骤都重新人工进图。新增覆盖策略带来的合理视觉变化须明确列出，不能用“必须与旧包完全同字节”替代参数和玩法保护检查。

## 首轮只读验证记录（2026-09-16）

| 输入 / 分析目标 | 实际发现 | 结果与范围 |
|---|---|---|
| C1M1 / C5M1 | l_0 一份补丁；基础图 9 个雾控制器、15 个雾区域、4 个后处理控制器 | 分析完成；显式资源根中找到预设资源候选；脚本、动态目标和 BSP/LMP revision 差异仍需后续判断 |
| C3M1 / C4M3 | h_0/l_0/s_0 三份补丁；基础图 3 个雾控制器、3 个雾区域、3 个后处理控制器 | 分析完成；本次未提供资源根，资源检查明确未执行；脚本与动态目标单列 |

两次分析均核对所选输入及伴随候选哈希，未修改地图；所有转换、烘焙、采样、打包和人工验收阶段为未执行。这里的清单是分析证据，不扩大当前转换支持声明。

基线测试为 260 项（257 通过、3 项 Windows 符号链接权限跳过）；本阶段最终为 287 项（284 通过、同样 3 项跳过）。独立审查发现并修复无匹配通配符未进入未解决目标提示的问题，已有回归测试。
