# 中文使用指南

0.3.0 将 C2M1 或 C6M1 的视觉迁移到内置 `c5m1-daylight-v1` 预设，无需 C5 BSP/LMP 参考文件。C6 默认覆盖原有天气和局部氛围，生成晴天版本；可显式选择保留原天气。默认先生成离线包，再由用户选择是否进行游戏内 HDR 反射采样。已有 C6 晴天最终包获用户基本无问题的反馈；每次新构建仍需独立实机验收。

## 1. 准备依赖与配置

需要 Windows、可从 PowerShell 调用的 Python 3.11+、完整且可正常启动的 L4D2 安装，以及与 L4D2 匹配的官方工具。Python 代码只依赖标准库，无需第三方 Python 包。源码 ZIP 不包含 Python、游戏资源、编译器或启动器。

在解压后的项目根目录运行命令。路径含空格可用引号；为了 BSPZIP 资源清单兼容，请给工具包和运行输出使用 ASCII 路径，例如 `E:/L4D2MapConverter`、`E:/L4D2Runs/c6-c5-001`。输出必须位于游戏和工具安装目录之外。

```powershell
python --version
python -m unittest discover -s tests -v
if (Test-Path -LiteralPath config.local.json) { throw '配置已存在；升级重试请按第 2.2 节创建新配置，不要覆盖旧运行的配置。' }
Copy-Item -LiteralPath examples/c6-c5.example.json -Destination config.local.json
notepad config.local.json
```

测试 C2 时改用 `examples/c2-c5.example.json`。示例中的 `E:/...` 都是占位位置，必须按自己的安装修改。JSON 路径建议使用 `/`；若使用反斜杠，应写成 `\\`。**所有相对路径以配置文件所在目录为基准**，不是以当前终端目录为基准。

以游戏安装根目录 `<gameRoot>` 为例，常见输入布局如下。若自己的版本缺少文件或实体结构不同，让 `check` 报告不兼容；不要通过改名、删掉模式或复制其他图文件绕过检查。

| 配置项 | 用途及常见位置 |
|---|---|
| `source_profile` | C6 用 `c6m1_riverbank`，C2 用 `c2m1_highway`；只支持这两张来源地图 |
| `preset` | `c5m1-daylight-v1`，随源码/Python 包提供的固定版本 C5 日光预设 |
| `atmosphere_policy` | C6 默认 `replace`：替换雨、风暴、雷声、闪电曝光、局部雾/后处理/检查点调色及雨声环境音；`preserve` 保留旧版天气和局部分区效果。C2 仍使用已接受配置，只支持 `preserve`，可省略 |
| `source_bsp` | C6：`<gameRoot>/left4dead2_dlc1/maps/c6m1_riverbank.bsp`；C2：`<gameRoot>/left4dead2/maps/c2m1_highway.bsp` |
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

推荐配置同时指定 `source_profile` 和 `preset`，不填写 `profile`、`reference_bsp` 或 `reference_lmp`。旧格式仍支持 `profile: c6-c5` 或 `c2-c5`，但须继续明确提供原 C5 `reference_bsp` 和 `reference_lmp` 路径。两套选择方式混用会拒绝；预设也不支持任意外部 JSON 路径。详见 [预设说明](presets.md)。

`replace` 保留建筑、物件、局部灯光/材质、碰撞、NAV、机关和推进条件。已核实的 C6 天气链会被移除；惊动新娘 Witch 的尸潮仍保留，只取消它的风暴分支。各雾区及后处理的效果统一为目标设置，原区域体积可作为技术载体保留。室外环境音改为 C5 水岸，室内改为 C5 商店室内底噪；不再沿用原雨声。不会增加新的降雨体积，也不宣称支持任意地图或解释任意脚本。

## 2. 检查并生成离线包

```powershell
python -m l4d2_bsp.workflow check --config config.local.json
```

这一步在内存中检查基础 BSP、三模式 LMP、预设（旧配置检查参考图）和所需风格资源，不写游戏目录。通过后执行：

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

### 2.1 从 0.1.0 的 C6 VHV 报错恢复

若旧版在 VRAD 结束后报告 `VHV topology changed: sp_hdr_....vhv`，先升级到 0.1.1 或后续包含该修复的版本。历史 C6 地图的缓存可能对应旧模型，而完整版游戏优先读取 DLC/update 中更新后的模型；这类差异需要结合模型资源校验，不应通过改头部、关闭检查或调整游戏正常挂载顺序绕过。

失败状态不能继续 `prepare-capture` 或安装地图。保留旧配置与运行目录，复制新配置并仅更改输出目录，再构建一次：

```powershell
$retryConfig = 'config.retry.local.json'
if (Test-Path -LiteralPath $retryConfig) { throw '重试配置已存在，请换一个新文件名。' }
$retryData = Get-Content -LiteralPath config.local.json -Raw | ConvertFrom-Json
$retryData.output_dir = './runs/c6-c5-02'
if (Test-Path -LiteralPath $retryData.output_dir) { throw '输出目录已存在，请选择一个新目录。' }
$retryData | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $retryConfig -Encoding UTF8
python -m l4d2_bsp.workflow build --config "$retryConfig"
```

上例要求新配置与原配置位于同一目录；后续第 2 节解析 `$runDir` 时，使用新配置文件名。**若协作者已经替你完成新构建，直接使用其提供的新运行目录，不要再次执行 build。**

0.1.1 会先显示模型资源检查进度，再进入 VRAD。只有能够从对应模型的 MDL v49、VVD v4、DX90 VTX v7 验证新光照布局的版本迁移才放行；无资源证据、错误索引、分段或校验值仍会失败。成功后可在 `run.json` 的 `bake_audit.model_lighting_migrations` 查看明细。使用到的资源记录在 `model_resource_inventory`；后续命令也会检查其内容及查询来源，运行期间不要更换这些资源。

模型资源检查按 `resource_roots` 顺序查询，BSP 内嵌资源优先；同一目录内出现内容不同的同名候选则拒绝。它验证编译结果与配置资源的一致性，不替代实际游戏的挂载检查。已有 addon 对模型的覆盖仍需在实机阶段排查。

### 2.2 从旧天气版本重新生成晴天 C6

旧的 `offline_ready`、采样文件和最终包不会随源码升级自动变成晴天。保留原配置、运行目录和采样证据，使用新配置及全新输出目录重新烘焙。不要把旧风暴版本采样到的反射导入新版。

若旧运行安装过临时采样文件，先彻底退出游戏，再归档并按收据清理。例如旧运行位于 `runs/c6-c5-02`：

```powershell
$oldRun = (Resolve-Path -LiteralPath './runs/c6-c5-02').Path
python -m l4d2_bsp.workflow collect-capture --run "$oldRun"
if ($LASTEXITCODE -ne 0) { throw '旧采样归档失败，先处理错误，不要手动删除临时 BSP。' }
python -m l4d2_bsp.workflow remove-capture --run "$oldRun"
```

`collect-capture` 只归档，不要求先生成旧版最终包。采样后重连失败也应先归档，由文件审计判断首次采样结果；不能因重连失败就删除证据。如果旧运行尚未安装采样文件，跳过上述两条命令。收据清理不删除已部署的旧版原名 VPK；按第 5 节停用它，避免与新版同图包冲突。

复制上一次实际使用的配置，**新配置必须与旧配置放在同一目录**，从而保持相对路径含义：

```powershell
$previousConfig = './config.c6-retry.local.json'  # 改成自己上次实际使用的配置
$newConfig = './config.c6-clear.local.json'
if (Test-Path -LiteralPath $newConfig) { throw '新配置已存在；若协作者已经准备好，请直接使用它，勿再次复制。' }
$newData = Get-Content -LiteralPath $previousConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$newData | Add-Member -NotePropertyName atmosphere_policy -NotePropertyValue replace -Force
$newData.output_dir = './runs/c6-c5-clear-01'
if (Test-Path -LiteralPath $newData.output_dir) { throw '输出目录已存在，请选择新目录名。' }
$newData | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $newConfig -Encoding UTF8
python -m l4d2_bsp.workflow check --config "$newConfig"
if ($LASTEXITCODE -ne 0) { throw '检查失败，先处理错误。' }
python -m l4d2_bsp.workflow build --config "$newConfig"
```

构建后，按第 2 节解析 `$runDir`，但将 `config.local.json` 换为此次新配置文件名。已经准备好新配置时，直接从 `check`/`build` 开始。`run.json` 顶层的 `atmosphere_policy` 应为 `replace`；`preflight.base_style.weather` 及各 `mode_styles` 记录天气移除明细。受支持原始 C6 每份实体数据移除 34 个天气实体、145 条天气相关输出，并添加一颗视觉太阳。

离线包已经重算光照，但反射仍来自原图。可先按第 5 节部署和观察它；如果只需要离线输出，到这里即可。记录的画面标注“旧反射”。

### 2.3 从参考文件配置迁移到内置预设

已开始的旧运行继续使用原配置与原参考文件，不修改其 `run.json` 或配置哈希。升级源码不会把旧运行改写为预设运行；仅归档已接受结果也不需要重新烘焙。

需要新构建时，把当前示例复制为同目录下一个全新配置文件，填写原来源地图、模式、NAV、游戏和工具路径，选择全新的 `output_dir`。使用 `source_profile` 与 `preset`，去掉旧 `profile`、`reference_bsp`、`reference_lmp` 三项，再执行 `check` 和 `build`。C6 如要保持旧天气必须明确设为 `preserve`；省略时为 `replace`。C2 仅支持 `preserve`。不要覆盖已经被旧运行追踪的配置。

## 3. 准备游戏内反射采样

工具不会启动游戏；请使用平时能够加载完整资源的正常启动器，并为本地采样会话配置 `-insecure`。不要为了采样换用另一套游戏目录或复制启动器。

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

**从重启开始的准备：**先完全退出游戏。在 Steam 的游戏属性→启动选项，或正常启动器提供的游戏启动参数中加入 `-insecure`，然后重新启动。自定义启动器必须把参数实际传给 `left4dead2.exe`，仅把参数写在启动器快捷方式上不一定生效。可以在 PowerShell 核对：

```powershell
Get-CimInstance Win32_Process -Filter "Name='left4dead2.exe'" | Select-Object ExecutablePath,CommandLine
```

确认是配置指定的游戏安装，并且游戏进程命令行包含 `-insecure`。它是启动参数，不是控制台命令；`sv_cheats 1`、`sv_lan 1` 都不能替代它。若启动器无法传递该参数，先调整启动器的参数设置，暂不采样。采样会话只用于本地测试；回到正常联机前退出游戏、移除该启动参数，并恢复自己的联网设置。

| 步骤 | 目的与操作 | 成功信号或停止条件 |
|---|---|---|
| 启动与加载 | 按上文带 `-insecure` 重启后，执行 `exec lmc_<runid>_load`；配置会设置 `sv_lan 1` 并加载合作模式 | 本次 `mc...` 别名地图；缺失模型、异常泛白等问题先停止 |
| 等待开场 | 等待开场结束、曝光和天气进入稳定状态 | 稳定场景；不要在开场过渡中采样 |
| 关闭镜面 | 执行 `mat_specular 0`，等待材质重新加载 | 重载结束，游戏恢复响应 |
| 检查状态 | 执行 `exec lmc_<runid>_check` | 本次必须有 `GUARD_PASSED`、无 `REFUSED`；实际别名、HDR=2、specular=0、tonemap 最大曝光=5、LAN=1、本地主机已连接。另人工确认 `status` 显示 `Windows Listen`、`insecure` 及已连接玩家 |
| 请求采样 | 只执行一次 `exec lmc_<runid>_capture` | `CAPTURE_REQUESTED` 仅说明发出请求；等待原生 cubemap 流程及随后地图重载。进度跑完不等于文件已验收 |
| 恢复设置 | 执行 `exec lmc_<runid>_finish` | 恢复 `mat_specular 1` 并打印状态；提示不代表文件已验收 |
| 退出与归档 | 彻底退出游戏，再执行下一节命令 | 游戏进程已退出，文件不再被引擎写入 |

`<runid>` 是说明用占位符，不能直接输入。准确命令见 `CAPTURE-STEPS.txt`。控制台和截图按键以自己的游戏配置为准。

默认晴天 C6 已移除核实过的原风暴控制。只有显式使用 `preserve` 时，旧风暴、雨、雷电和事件曝光仍保留；它们可能在 guard 后变化，影响反射。无论何种策略，一次 guard 通过都不证明反射画面正确；守卫也不能直接核验启动参数或 VAC 状态。

**采样后的地图重载与插件提示：**原生 `buildcubemaps` 会尝试重载地图。若此时回到菜单、提示先移除插件、拒绝连接或显示 0 玩家，不要再次执行 `_capture`，不要删除插件，也不必重新进图。先在菜单控制台执行本次 `_finish` 恢复镜面，再彻底退出游戏，按第 4 节归档和审计。后续空会话的 `REFUSED` 不会自动否定先前的有效请求；工具会检查日志顺序及实际 BSP 资源。日志里重载前后从 `insecure` 变为 `secure`，说明需要重新检查启动参数是否传递并持续生效。

单次请求守卫只在当前地图会话内有效，原生重载会重置它。新版导入拒绝同一日志出现多个采样请求，以免混淆反射来源。确实需要重新采样时，先归档，再建立新运行/别名；当前没有跳过烘焙的采样重试命令。不要清空日志、伪造标记或修改清单绕过验证。

## 4. 归档、审计并生成最终包

游戏退出后执行：

```powershell
python -m l4d2_bsp.workflow collect-capture --run "$runDir"
$runData = Get-Content -LiteralPath (Join-Path $runDir 'run.json') -Raw | ConvertFrom-Json
$captureEvidence = $runData.latest_capture_evidence
python -m l4d2_bsp.workflow finish-capture --run "$runDir" --bsp "$captureEvidence/captured.bsp" --log "$captureEvidence/capture.log"
```

`collect-capture` 把安装目录内的别名 BSP 和 `lmc_<runid>_capture.log` 归档到 `evidence/attempt-01/`，再次收集使用新 attempt 目录。缺少日志、错误 run/map 标记或未知资源格式时应检查操作，不要自行补造成功标记。

`finish-capture` 检查同一 run/别名的“地图→守卫通过→唯一采样请求”顺序、受保护 BSP 数据、原资源、HDR 采样坐标和 VTF RGB，再用官方 BSPZIP 回填到原地图名 BSP，生成并解包核对最终 VPK。`reflection_import.runtime_log` 区分请求前后的拒绝与原生重载。通过后的 `final_ready_pending_user_validation` 表示工具审计完成，等待实机验收。

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

确认原地图名、天空 `sky_l4d_c5_1_hdr`、HDR 已启用且级别为 2、镜面为 1。若挂载或渲染状态不符，先解决再比较。检查开场、车辆/枪械反光、地面和建筑阴影、天空/远景雾、室内外曝光过渡，再走完可测试路线和事件。`replace` 下应没有原暴雨、雷声、闪电曝光和雨声环境音；检查新娘 Witch 被惊动后的尸潮仍正常。`preserve` 下原天气仍存在属于预期。三模式 LMP 静态处理不代表三种模式均已实机验收。

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
| 采样后重载提示移除插件、拒绝连接或 0 玩家 | 不重复采样、不删插件；菜单执行 `_finish`、退出并归档，交给 BSP 审计判断。下次检查进程 `-insecure` 与 `status` |
| 泛白、反射资源缺失、采样中崩溃 | 不验收；恢复 `mat_specular 1`，退出并保存证据，检查安装/挂载 |
| 同一会话拒绝再次采样，或日志有多个请求 | 保留并归档本次证据；重试用新运行/别名，勿清空日志。原生重载后也不再次请求 |
| 捕获内容审计拒绝 | 核对本次别名、是否完成捕获及天气，重新采样后收集新 attempt |
| BSPZIP/打包导入失败 | 查看 `last_capture_import_error` 和对应 attempt 日志；修正原因后可重试同一捕获 |

先运行 `python -m l4d2_bsp.workflow status --run "$runDir"`；状态检查也会核对哈希。反馈问题可提供脱敏状态、错误、版本和必要截图，完整清单/日志含本机路径，分享前先检查。
