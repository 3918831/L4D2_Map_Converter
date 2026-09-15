# 无注入自动 HDR 反射采样

0.5.0 增加可选 `auto-capture`：接管原来需要启动游戏、执行 CFG、等待采样、退出、归档和回填的操作。仍由完整游戏实际渲染 HDR cubemap；无需 `l4d2-portal` 接口、DLL 注入或键鼠模拟。最终安装包的视觉与玩法验收继续由使用者完成。

## 适用范围

- Windows、Python 3.11+、可正常启动的完整游戏以及官方工具。Python 代码仅使用标准库。
- 自动模式当前要求 `atmosphere_policy: replace`，适用于已支持的 C2→C4 固定阴天和 C6→C5 晴天。新增 [通用转换入口](generic-conversion.zh-CN.md) 同样接入自动流程，曝光控制器来自本轮方案；这不扩大人工验收范围。C2→C5 的 `preserve` 路径继续使用手动采样。
- 已验证的启动方式是使用者已有的 ColdClient INI 启动器。`executable` 适配器提供显式传参接口，但未据此宣称 Steam 正式版启动已实测通过。
- 已有插件不由工具删除。干扰开场状态、CFG、材质或 VScript 的插件可能使检查拒绝，届时保留证据并使用手动路径排查。

## 首次配置

先按 [C5M1 指南](guide.zh-CN.md) 或 [C4 指南](c4m3-guide.zh-CN.md) 创建新的转换配置，执行 `check`、`build`。`build` 不启动游戏。既有运行的配置和产物不可修改。

将启动配置示例复制到项目根目录，再编辑其中所有路径：

```powershell
Copy-Item examples/launcher.coldclient.example.json launcher.local.json
notepad launcher.local.json
```

`backend: coldclient` 适用于正常启动依赖 `ColdClientLoader.ini` 的机器。工具仅临时追加该文件中的 `ExeCommandLine`，启动后恢复原字节；不提供或下载启动器。若本机可以直接启动游戏 EXE，可参考 `launcher.executable.example.json`，先自行确认其能正常启动；此方式要求 `executable` 与 `game_executable` 指向同一文件。

| 配置项 | 作用 |
|---|---|
| `executable` | 平时能正常启动该安装的 EXE；不是任意替代启动器 |
| `game_executable` | 本次 `game_dir` 上一级的 `left4dead2.exe`，用于核对真实进程路径 |
| `ini_path` | 仅 ColdClient 方式需要，指向原启动器 INI |
| `arguments` | 附加进程参数数组；自动追加 `-insecure +exec <本轮启动CFG>`。不得自行加入 `+exec`、`+map`、`+lservercfgfile` |
| `listenserver_cfg` | 平时有效的 `lservercfgfile` 值，通常为 `listenserver.cfg`。自动 ready CFG 会先执行此文件，结束时恢复这个设置；自定义过该值时必须如实填写 |
| `startup_timeout_seconds` | 等待实际游戏进程出现，默认 60 秒 |
| `timeout_seconds` | 启动完成后等待整个采样与退出，默认 600 秒，最多 3600 秒 |
| `stop_timeout_seconds` | 失败时等待正常退出，默认 60 秒 |
| `stable_seconds` | 玩家恢复控制、材质/曝光设置稳定各自需要连续观察的秒数，默认 5，范围 1–60 |

路径相对于启动配置文件解析。`stable_seconds` 是保守等待，不是 GPU 完成信号，也不能证明所有插件下的曝光已收敛。首次使用一套机器配置后仍应检查车辆、武器、材质和反射是否异常。

## 一条命令完成采样

完全退出游戏；保持 Steam 或正常启动器所需的其他依赖就绪。对已 `build` 成功的运行执行：

```powershell
python -m l4d2_bsp.workflow auto-capture --run ./runs/c2-c4-01 --launcher launcher.local.json
python -m l4d2_bsp.workflow status --run ./runs/c2-c4-01
```

把运行路径换成配置中的真实 `output_dir`。也可以从尚未安装的 `capture_prepared` 状态开始。不要先执行 `install-capture`，也不要在自动运行时手动加载其他地图或执行采样命令。游戏可能显示窗口、加载和重载地图，最后正常退出。

成功依次完成：准备唯一别名 → 安装临时资源 → 正常启动并核验路径/参数 → 开场结束与材质等待 → 原守卫检查 → 一次原生采样 → 重载后文件审计 → 退出 → 归档 → 原名回填/打包 → 清理临时文件。

最终 VPK 路径在 `run.json` 的 `final_package.vpk` 中。状态仍是 `final_ready_pending_user_validation`。工具不会自动安装最终 VPK，也不会把机器成功写成用户验收。按原指南部署原名最终包并检查画面和游玩过程。

## 超时、中断和恢复

本轮目录内的 `auto-capture/attempt.json` 记录启动配置、阶段、请求、审计和错误；`launch.json` 与 `launcher-original.ini` 用于启动器恢复。`evidence/attempt-*`、`reflection-import/attempt-*` 保留正常导入证据，`auto-capture/cleanup-evidence` 保留退出后的清理证据。不要删除这些文件以绕过重复运行保护。

失败后工具先请求正常退出，不强制终止游戏。若控制器不可用或游戏卡住，手动完全退出游戏，然后执行：

```powershell
python -m l4d2_bsp.workflow recover-auto-capture --run ./runs/c2-c4-01
```

恢复只负责 STOP、启动器恢复和有收据的清理；不会启动游戏，也不会再次采样。若 INI 或安装文件被外部修改，保留当前文件和原备份，先检查冲突；工具不覆盖外部修改。对不确定采样结果的重试，创建新的转换配置和输出目录重新构建，不复用旧别名。已经成功的运行无需恢复。

本版未提供断电后自动续跑、通用挂机监控或后台服务。无法自动退出时仍需人工关游戏。若自动启动方式不适合你的安装，保留原有 `prepare-capture` / `install-capture` / 人工 CFG / `collect-capture` / `finish-capture` / `remove-capture` 流程。

运行目录和游戏目录的微小锁文件用于防止并发操作；进程退出后锁自动释放，不需要手动删除锁文件。
