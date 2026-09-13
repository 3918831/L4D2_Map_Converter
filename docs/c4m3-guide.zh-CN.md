# C4M3 固定阴天：C2M1 使用指南

0.4.0 提供 `c4m3-overcast-static-v1`，将 C4M3 初始化时的固定阴天参数应用到**原始** C2M1 `c2m1_highway`。首轮人工 HDR 反射采样、原名回填和用户实机验收已完成；新构建仍须独立检查。C5 预设及已接受结果继续保留。

2026-09-13 首轮离线验证已完成：基础与 h/l/s 四份输出各有 135 项字段/参数变化，重复应用不变；C2/C6→C5 八组历史输出逐字节保持。测试套件 205 项中 204 项通过、1 项因 Windows 符号链接权限跳过，解压源码包复测同样通过。独立审查发现的未知阴影写入者遗漏已修复并补回归。

本机运行 `1b6530c3c5454b7c` 完成原生 HDR 四跳/静态模型烘焙；BSP 为 26,560,724 字节，SHA256 `53ad24d98c68a61774a01b115e6d02beb78a62aa571bd75588ef36beb5704d1f`；八文件离线 VPK SHA256 `37b77824bba94c32e11c845ea6531cfe6146e94c74e61640e6599be702b26b2e`。受保护 lump、面拓扑、静态模型布置和 detail prop 布置审计通过，原生包解出核对通过。采样别名 `mc1b6530c3` 已完成引擎捕获；63 份 HDR 反射全部更新并通过导入审计，141 个临时文件按收据清理。最终八文件 VPK SHA256 为 `11d721fc850e91eade4d0f832e484c75aa45b2b051bd7a20a8c82923ded00995`，原生解包与安装哈希核验通过。用户对原名最终包确认“没什么问题，C4风格化可验收”。完整身份和测试边界见 [验证记录](validation.md#c2c4-首次独立验收记录)。新用户运行会获得自己的 ID 和命令，不能复用这里的别名。

## 1. 目标和边界

本版固定天空、暗光、雾、调色、曝光及后处理，保留普通风；不新增降雨区域、雨粒子、雷声、闪电、强风暴、震动或天气循环。参数取自 C4 的初始化基础状态，不等同于原关卡任意时刻的动态画面。

| 项目 | 固定目标 |
|---|---|
| 天空 | `sky_l4d_c4m4_hdr`，这是实际资源名 |
| 主调色 | `materials/correction/cc_c4_return.raw`，覆盖主、开场和检查点等入口 |
| 环境直射 / 漫射 | `200 225 230 10` / `79 108 104 5`；保留其方向与 HDR 回退定义 |
| 基础雾 | color `20 25 25`，start `0`，end `1500`，density `1`，farz `2000` |
| 默认后处理 | localcontrast `-.25`，edges `-.3`；采用默认控制器基础效果 |
| 曝光 | max `10`，min `1`，global rate `.25`；不设 C5 的 bright-pixels 值 |
| 太阳 | 无 `env_sun` 光斑；仍有方向光及阴影 |
| 普通风 | speed 15–30、gust 50–100，方向 `0 -180 0` |

天空雾独立于世界雾；不会把世界雾 end 1500 写作天空雾距离。名称含 `storm` 的默认控制器可以提供上述静态基础值，但不导入其动态 I/O。闪电的曝光 50、风暴浓雾及强风不属于本预设。

来源局部雾、后处理、调色和环境音默认覆盖；局部灯光和材质保留。建筑、物件、天空几何、碰撞、NAV、机关、尸潮、车辆警报和发电机声音保持受保护行为。开场镜头和控制器切换时序保留，不为改变颜色删除整个开场。

室内声音取自 C4 soundscape 的非天气 room-tone 子树。室外剔除雨、雷和风暴后有意不加入环境循环声；不使用 C5 海鸥声，也不把工业火焰等局部场景声音铺满地图。声音引用与资源随运行审计，不分发 Valve 音频或整套关卡脚本。

## 2. 使用原始输入创建新运行

依赖与 [通用指南](guide.zh-CN.md) 相同：Windows、Python 3.11+、完整 L4D2 和匹配的官方工具。工具包及输出使用游戏目录外的 ASCII 路径。下列命令在项目根目录执行；若协作者已准备本次配置或构建，直接使用提供的路径，不重复复制或构建。

```powershell
if (Test-Path -LiteralPath config.c2-c4.local.json) { throw '本次配置已存在，请直接检查现有配置，或为新运行选择新名称。' }
Copy-Item -LiteralPath examples/c2-c4.example.json -Destination config.c2-c4.local.json
notepad config.c2-c4.local.json
```

修改示例全部本机路径，保留以下选择：

```json
{
  "source_profile": "c2m1_highway",
  "preset": "c4m3-overcast-static-v1",
  "atmosphere_policy": "replace"
}
```

该代码块仅为选择项；完整模板是 `examples/c2-c4.example.json`。`source_bsp` 必须指向安装中的原始 C2 BSP，`mode_lmps` 提供原始 h/l/s 三文件，另提供原始 NAV 和可选 exclude。不要使用已转换为 C5 的 BSP/LMP，也不要修改旧 C5 配置或复用其输出目录。`game_dir` 指向含 `gameinfo.txt` 的完整资源安装，`tools_dir` 包含官方 VRAD/BSPZIP/VPK；不需要 C4 donor BSP/LMP。

只开放 C2→C4 的 `replace` 组合；C6→C4 与 `preserve` 会被拒绝。所有相对路径以配置文件所在目录为基准；缺少 exclude 时设为 `null`，其他必需输入不能省略。

```powershell
python -m l4d2_bsp.workflow check --config config.c2-c4.local.json
if ($LASTEXITCODE -ne 0) { throw '检查失败，先处理错误。' }
python -m l4d2_bsp.workflow build --config config.c2-c4.local.json
if ($LASTEXITCODE -ne 0) { throw '构建失败，保留日志和输出；修复后使用新输出目录。' }
$configFile = (Resolve-Path -LiteralPath config.c2-c4.local.json).Path
$configData = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
$configBase = Split-Path -Parent $configFile
$runDir = [IO.Path]::GetFullPath([IO.Path]::Combine($configBase, $configData.output_dir))
python -m l4d2_bsp.workflow status --run "$runDir"
```

`check` 检查基础及三模式身份、目标字段和资源；`build` 完成原生 HDR 重烘焙、保护审计和打包解出核对后才成为 `offline_ready`。离线包仍含原图反射。运行保存配置、输入哈希和 `preset.json`，开始后不要改动这些文件。

提供 exclude 时，C4 原名包应有八文件：BSP、h/l/s 三 LMP、NAV、exclude、addon 描述和 `scripts/soundscapes_c2m1_highway.txt`。没有 exclude 时为七文件。C5 的既有七文件布局（含 exclude）保持不变。

## 3. 独立测试和人工反射采样

先退出游戏，仅停用已确认包含 `c2m1_highway` 的旧 C5 测试 addon，并记录其路径、哈希和备份位置。保留原 C5 预设、配置、运行目录及已接受 VPK；不要删除官方地图或整个 addons 目录。测试 C4 时不要同时挂载同图 C5 包。

以下流程仅在本次运行达到 `offline_ready` 后使用：

```powershell
python -m l4d2_bsp.workflow prepare-capture --run "$runDir"
if ($LASTEXITCODE -ne 0) { throw '采样准备失败，先处理错误。' }
python -m l4d2_bsp.workflow install-capture --run "$runDir"
if ($LASTEXITCODE -ne 0) { throw '采样安装失败，先处理错误。' }
Get-Content -LiteralPath (Join-Path $runDir 'CAPTURE-STEPS.txt')
```

采样安装包含唯一地图别名和对应 `scripts/soundscapes_<别名>.txt`，最终包使用原地图名及原名声音脚本。工具只通过明确安装命令写入本次临时文件，游戏由用户启动。

严格执行本次 `CAPTURE-STEPS.txt` 的完整步骤：使用正常启动器带 `-insecure` 完整重启，核对实际 `left4dead2.exe` 命令行和 `status` 的本地不安全会话，加载本次别名，等待开场稳定，关闭镜面后执行检查。C4 guard 期望最大曝光 **10**；通用 C5 指南中的 5 不适用于本次运行。不要把闪电瞬间的 50 当作正常目标，也不要手动改曝光绕过 guard。

仅在本次 guard 通过后请求一次采样，等待原生流程及地图重载，执行恢复命令并退出游戏。采样后重连拒绝不要求再次采样或删除插件。按 [通用指南第 4 节](guide.zh-CN.md#4-归档审计并生成最终包) 使用本次 `$runDir` 归档、导入、审计和清理；不要导入 C5 或其他运行的捕获。`final_ready_pending_user_validation` 表示文件审计完成，仍须原名验收。

## 4. 原名验收与回退

按通用指南第 5 节从本次 `run.json` 选择、核验并安装原名 VPK，退出游戏后切换 addon。完整重启并加载：

```text
mat_hdr_level 2
mat_specular 1
map c2m1_highway coop
```

核对 `status`、`sv_skyname`、`mat_hdr_enabled`、`mat_hdr_level` 和 `mat_specular`：地图应为 `c2m1_highway`，天空为 **`sky_l4d_c4m4_hdr`**，HDR 启用、级别 2、镜面 1。通用 C5 指南的天空名不适用于本预设。

等待开场后观察室内外曝光、暗区层次、静态模型、远景雾、地面和建筑阴影、车辆与武器反光；确认无太阳光斑、雨雷和动态风暴效果。室外没有新增循环环境声属于设计目标，室内应保留所选底噪，警报与发电机等事件声音应正常。随后检查原开场及可测试路线、机关和尸潮事件；基础与 h/l/s 的静态审计不等于全部模式实机通过。

记录 run ID、包 SHA256、模式、画质和同机位原始截图，并注明“离线旧反射”或“本次新 HDR 反射”。用户确认前只报告实际完成的阶段，不以参数报告或打包成功宣称画面无劣化。

回退时先退出游戏，移出本次精确的 C4 addon，再恢复记录中的旧 C5 addon并完整重启。临时采样文件用本次 `remove-capture` 按收据清理，保留运行和已归档证据。
