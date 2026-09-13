# 中文使用指南

本版将 C2M1 或 C6M1 的全局视觉迁移到 C5 风格。默认先生成可加载的离线包，再由用户选择是否进行游戏内 HDR 反射采样。C6 是下一张待独立实机验收的地图，本指南没有宣称它已通过画面或玩法测试。

## 1. 准备依赖与配置

需要 Windows、可从 PowerShell 调用的 Python 3.11+、完整且可正常启动的 L4D2 安装，以及与 L4D2 匹配的官方工具。Python 代码只依赖标准库，无需第三方 Python 包。源码 ZIP 不包含 Python、游戏资源、编译器或启动器。

在解压后的项目根目录运行命令。路径含空格可用引号；为了 BSPZIP 资源清单兼容，请给工具包和运行输出使用 ASCII 路径，例如 `E:/L4D2MapConverter`、`E:/L4D2Runs/c6-c5-001`。输出必须位于游戏和工具安装目录之外。

```powershell
python --version
python -m unittest discover -s tests -v
Copy-Item -LiteralPath examples/c6-c5.example.json -Destination config.local.json
notepad config.local.json
```

测试 C2 时改用 `examples/c2-c5.example.json`。示例中的 `E:/...` 都是占位位置，必须按自己的安装修改。JSON 路径建议使用 `/`；若使用反斜杠，应写成 `\\`。**所有相对路径以配置文件所在目录为基准**，不是以当前终端目录为基准。

以游戏安装根目录 `<gameRoot>` 为例，常见输入布局如下。若自己的版本缺少文件或实体结构不同，让 `check` 报告不兼容；不要通过改名、删掉模式或复制其他图文件绕过检查。

| 配置项 | 用途及常见位置 |
|---|---|
| `profile` | C6 用 `c6-c5`，C2 用 `c2-c5` |
| `source_bsp` | C6：`<gameRoot>/left4dead2_dlc1/maps/c6m1_riverbank.bsp`；C2：`<gameRoot>/left4dead2/maps/c2m1_highway.bsp` |
| `reference_bsp` | `<gameRoot>/left4dead2/maps/c5m1_waterfront.bsp` |
| `reference_lmp` | `<gameRoot>/update/maps/c5m1_waterfront_l_0.lmp` |
| `mode_lmps` | 必须提供 `h`、`l`、`s` 三项，对应 `<gameRoot>/update/maps/<原地图名>_h_0.lmp` 等三文件 |
| `nav` | `<gameRoot>/update/maps/<原地图名>.nav` |
| `exclude` | 可选的原图 `<原地图名>_exclude.lst`；没有则用 `null` |
| `game_dir` | `<gameRoot>/left4dead2`，须含 `gameinfo.txt`，不是安装根目录 |
| `tools_dir` | 官方工具安装的 `bin`，须含 `vrad.exe`、`bspzip.exe`、`vpk.exe` 及运行依赖 |
| `output_dir` | 本次全新运行目录；工具拒绝复用已存在目录 |
| `threads` | VRAD 线程数，1—64，默认 4 |
| `timeout_seconds` | VRAD 最长秒数，1—86400，默认 3600；按机器性能设置 |
| `resource_roots` | 可选资源查询目录列表；省略时查找游戏安装的 update、DLC、基础目录 |

`resource_roots` 查询只说明资源可找到，不能证明游戏最终挂载优先级，也不是全部材质递归依赖检查。工具和运行游戏应指向准备实际测试的兼容完整资源环境。

## 2. 检查并生成离线包

```powershell
python -m l4d2_bsp.workflow check --config config.local.json
```

这一步在内存中检查基础 BSP、三模式 LMP、参考图和所需风格资源，不写游戏目录。通过后执行：

```powershell
python -m l4d2_bsp.workflow build --config config.local.json
```

烘焙使用 HDR、四跳反弹、静态模型光照、模型几何遮挡和纹理阴影，可能耗时较长。进度输出给出 `bake/vrad.log` 的位置。工具完成结构审计、官方 VPK 打包及逐文件解包字节验证后，状态为 `offline_ready`。

以下命令根据配置解析运行目录，后续命令复用 `$runDir`：

```powershell
$configFile = (Resolve-Path -LiteralPath config.local.json).Path
$configData = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
$configBase = Split-Path -Parent $configFile
$runDir = [IO.Path]::GetFullPath([IO.Path]::Combine($configBase, $configData.output_dir))
python -m l4d2_bsp.workflow status --run "$runDir"
```

`run.json` 记录配置快照及输入哈希，后续处理会重新检查。**从 build 开始，不要编辑配置、替换输入或修改追踪的输出文件**。需要变更时保留本次运行，复制新配置并选择新的 `output_dir`。失败烘焙不会自动续跑，也不要手动把状态改成成功。

离线包已经重算光照，但反射仍来自原图。可先按第 5 节部署和观察它；如果只需要离线输出，到这里即可。记录的画面标注“旧反射”。

## 3. 准备游戏内反射采样

工具不会启动游戏；请使用平时能够加载完整资源的正常启动方式。

```powershell
python -m l4d2_bsp.workflow prepare-capture --run "$runDir"
```

状态变为 `capture_prepared`，`capture/` 包含唯一地图别名、同版本 NAV/模式文件、旧反射路径别名，以及四个 CFG 和两个 VScript 控制文件。旧反射别名用于保证临时图能找到原材质引用，不是新捕获结果。别名只用于采样，最终玩法测试用原地图名。

彻底退出 L4D2，然后执行：

```powershell
python -m l4d2_bsp.workflow install-capture --run "$runDir"
Get-Content -LiteralPath (Join-Path $runDir 'CAPTURE-STEPS.txt')
```

这是明确写入游戏安装的命令，只安装本次新增的临时文件，遇到已有目标或可检测资源冲突就拒绝覆盖。状态应为 `capture_installed`。使用打印的本次 run ID 命令，不要复制其他运行的 ID。

按顺序操作，每一步完成后再进行下一步：

| 步骤 | 目的与操作 | 成功信号或停止条件 |
|---|---|---|
| 启动与加载 | 正常启动这套游戏，打开开发者控制台，执行说明中的 `exec lmc_<runid>_load` | 本次 `mc...` 别名地图、合作模式；缺失模型、异常泛白等问题先停止 |
| 等待开场 | 等待开场结束、曝光和天气进入稳定状态 | 稳定场景；不要在开场过渡中采样 |
| 关闭镜面 | 执行 `mat_specular 0`，等待材质重新加载 | 重载结束，游戏恢复响应 |
| 检查状态 | 执行 `exec lmc_<runid>_check` | 必须有 `GUARD_PASSED`，无 `REFUSED`；实际别名、HDR=2、specular=0、tonemap 最大曝光=5 |
| 请求采样 | 执行 `exec lmc_<runid>_capture` | `CAPTURE_REQUESTED` 仅说明发出请求；等待原生 cubemap 流程完成、游戏恢复正常 |
| 恢复设置 | 执行 `exec lmc_<runid>_finish` | 恢复 `mat_specular 1` 并打印状态；提示不代表文件已验收 |
| 退出与归档 | 彻底退出游戏，再执行下一节命令 | 游戏进程已退出，文件不再被引擎写入 |

`<runid>` 是说明用占位符，不能直接输入。准确命令见 `CAPTURE-STEPS.txt`。控制台和截图按键以自己的游戏配置为准。

C6 保留风暴、雨和闪电，以及事件驱动的曝光变化。检查脚本只能核对采样开始时状态，不能保证过程中天气不变。若采样受闪电或过渡影响，记录现象并重新加载到合适状态；一次 guard 通过不证明反射画面正确。

## 4. 归档、审计并生成最终包

游戏退出后执行：

```powershell
python -m l4d2_bsp.workflow collect-capture --run "$runDir"
$runData = Get-Content -LiteralPath (Join-Path $runDir 'run.json') -Raw | ConvertFrom-Json
$captureEvidence = $runData.latest_capture_evidence
python -m l4d2_bsp.workflow finish-capture --run "$runDir" --bsp "$captureEvidence/captured.bsp" --log "$captureEvidence/capture.log"
```

`collect-capture` 把安装目录内的别名 BSP 和 `lmc_<runid>_capture.log` 归档到 `evidence/attempt-01/`，再次收集使用新 attempt 目录。缺少日志、错误 run/map 标记或未知资源格式时应检查操作，不要自行补造成功标记。

`finish-capture` 检查捕获来源、受保护 BSP 数据、原资源、HDR 采样坐标和 VTF RGB，再用官方 BSPZIP 回填到原地图名 BSP，生成并解包核对最终 VPK。通过后的 `final_ready_pending_user_validation` 表示工具审计完成，等待实机验收。

导入失败保留在编号的 `reflection-import/attempt-XX/`；错误可见 `last_capture_import_error`。修正外部问题后可对同一已归档捕获重试 `finish-capture`，它使用新的导入/打包 attempt，不覆盖旧证据。若拒绝原因是捕获内容本身，需要重新采样并收集新的 evidence，而不是反复导入同一错误文件。

归档后可以清理临时安装：

```powershell
python -m l4d2_bsp.workflow remove-capture --run "$runDir"
```

清理要求游戏退出，只删除收据中记录且哈希符合预期的临时文件。捕获后的 BSP 必须先归档，未归档改动阻止清理。清理不依赖旧输入仍存在；离线和最终 addon 不受影响。游戏生成的原始 capture 日志可能仍留在安装目录，归档副本保留在运行目录。

## 5. 安装原名 VPK 与独立测试

彻底退出游戏，停用或移出所有包含同一原地图名的旧测试 addon 并保留备份。每次只启用一个待比较版本；不要把临时别名 BSP 当发行地图，不覆盖安装自带的官方 BSP。

下面从清单选择最终包；若还没有最终包则选择离线包。核对包哈希后复制到配置中的 `addons`，拒绝覆盖同名文件：

```powershell
$runData = Get-Content -LiteralPath (Join-Path $runDir 'run.json') -Raw | ConvertFrom-Json
$selectedPackage = $runData.final_package
if ($null -eq $selectedPackage) { $selectedPackage = $runData.offline_package }
if ($null -eq $selectedPackage) { throw '本次运行尚无可部署包。' }
$packagePath = (Resolve-Path -LiteralPath $selectedPackage.vpk).Path
if ((Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash -ne $selectedPackage.sha256) { throw 'VPK 哈希不符。' }
if (Get-Process -Name left4dead2 -ErrorAction SilentlyContinue) { throw '请先彻底退出游戏。' }
$addonDir = Join-Path $runData.config.game_dir 'addons'
$addonPath = Join-Path $addonDir ([IO.Path]::GetFileName($packagePath))
if (Test-Path -LiteralPath $addonPath) { throw '目标已存在，请先确认当前部署。' }
New-Item -ItemType Directory -Path $addonDir -Force | Out-Null
Copy-Item -LiteralPath $packagePath -Destination $addonPath
$addonPath
```

此命令不能自动识别“不同文件名但包含同一地图”的旧插件，停用冲突包仍需人工确认。复制后完整重启游戏，确认 addon 启用，再加载原地图：

```text
mat_hdr_level 2
mat_specular 1
map c6m1_riverbank coop
```

C2 使用 `map c2m1_highway coop`。等待加载和材质重载结束，至少执行以下只读检查，保存输出以确认当前地图、天空和 HDR 状态：

```text
status
sv_skyname
mat_hdr_enabled
mat_hdr_level
mat_specular
```

确认原地图名、天空 `sky_l4d_c5_1_hdr`、HDR 已启用且级别为 2、镜面为 1。若挂载或渲染状态不符，先解决再比较。检查开场、车辆/枪械反光、地面和建筑阴影、天空/远景雾、室内外曝光过渡，再走完可测试路线和事件。C6 原有雨、风暴或闪电仍存在属于保留行为。三模式 LMP 静态处理不代表三种模式均已实机验收。

切换原版、离线包、最终包或回退版本时，都先退出游戏并停用当前同图 addon，再完整重启，避免纹理缓存误判。回退时，把本次精确的 `$addonPath` 移出 `addons` 到自己的备份目录，恢复之前的包；不要删除官方地图或整个 `addons`。

## 6. 固定机位与证据

本版可比较原版、离线包、完成反射的包；尚未实现 S0—S7 八阶段包生成或顺序演示器，也没有内置 C6 摄像机坐标。

先在待测试地图版本中选择视角，控制台输入 `getpos`，将输出的 `setpos ...; setang ...` 原样记录。之后在同一地图版本的各包中，先在本地控制台执行 `sv_cheats 1`，再粘贴自己记录的完整 `setpos`/`setang` 命令。完成观察后可执行 `sv_cheats 0` 恢复普通玩法测试。演示设置只用于本地观察，记录其使用情况，不写入交付 BSP。

每个机位记录：机位 ID、地图名、run ID、VPK SHA256、`setpos`/`setang`、FOV、分辨率、HDR/画质、HUD、开场/天气阶段和截图时间。使用自己平台支持的 PNG 截图方式保存原图，不假设某个功能键必然截图，不将后期调色图作为原始证据。

首轮 C6 可自选开场室外、车辆反光、建筑/地面阴影、天空/远景、室内外过渡五类视角。动态角色、雨、粒子和曝光影响帧间差异，说明无法固定的条件。命名可用 `c6-c5-001_offline_view01.png`、`c6-c5-001_final_view01.png`。记录模板见 [验证说明](validation.md)。

## 7. 常见中断与继续方式

| 情况 | 处理 |
|---|---|
| `check` 缺输入/工具/资源或配置不兼容 | 修正未开始运行的配置/依赖，再检查；不改造官方输入绕过约束 |
| VRAD 超时、缺模型材质、保护审计拒绝 | 保留清单和 `bake/vrad.log`；检查原因，修正后使用新配置/输出目录构建 |
| 配置/输入/产物哈希变化 | 本次不能继续处理；建立新运行，勿修改清单中的哈希。临时清理可按收据执行 |
| 安装拒绝已有文件或游戏运行中 | 退出游戏，确认冲突所属运行和 addon；按对应运行清理，避免覆盖 |
| 检查 `REFUSED` 或缺少 NetProps/API | 核对别名、HDR、镜面和曝光，不绕过 guard；版本不兼容时保存日志 |
| 泛白、反射资源缺失、采样重启/崩溃 | 不验收；恢复 `mat_specular 1`，退出并保存证据，检查安装/挂载后重启 |
| 同一地图会话拒绝再次采样 | 一次会话只允许一次请求；保留证据，重新加载并重新检查 |
| 捕获内容审计拒绝 | 核对本次别名、是否完成捕获及天气，重新采样后收集新 attempt |
| BSPZIP/打包导入失败 | 查看 `last_capture_import_error` 和对应 attempt 日志；修正原因后可重试同一捕获 |

先运行 `python -m l4d2_bsp.workflow status --run "$runDir"`；状态检查也会核对哈希。反馈问题可提供脱敏状态、错误、版本和必要截图，完整清单/日志含本机路径，分享前先检查。
