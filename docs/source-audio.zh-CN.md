# 保留来源地图声音（generic-replace-v4）

四套预设的新建配置推荐使用 `conversion: generic-replace-v4`。C5M1、C4M3、C7M1、C10M3 各自的视觉参数保持；声音改为来源地图的普通环境音，并剔除明确识别的天气层。不会再因选择 C5M1 而将非水岸地图替换成海鸥和波浪背景。

## 配置和迁移

使用 `examples/generic.example.json`（C5M1）、`generic-c4m3.example.json`、`generic-c7m1.example.json` 或 `generic-c10m3.example.json`，修改全部本机路径并选择全新的 `output_dir`：

```powershell
python -m l4d2_bsp.workflow check --config config.audio.local.json
python -m l4d2_bsp.workflow build --config config.audio.local.json
python -m l4d2_bsp.workflow auto-capture --run ./runs/my-audio-v4-01 --launcher launcher.local.json
```

配置仍要求完整游戏资源，`--run` 必须换成该配置实际的输出目录。首次启动设置见 [自动采样指南](auto-capture.zh-CN.md)。本轮声音处理不需要参考地图 BSP，也不需要录音作为输入。

旧 `generic-replace-v1/v2/v3`、旧来源适配器、预设 v1 JSON 和已接受的 VPK 保持原来的语义。为了可复现，不修改已有运行清单。`generic-v1/v2/v3.example.json` 留作旧规则示例；新规则应用于四套目标预设，而非按 C4/C6 等来源地图名添加特例。

## 保留与移除

- 保留普通循环声、随机声的剩余条目、音量、音调、时间间隔、空间参数及嵌套关系；原图自己的海鸥、水声等地域声音也保留。
- 保留 `env_soundscape` / `env_soundscape_triggerable` 的位置、半径、实体名称、启停和 OnPlay 等 IO；只把 `soundscape` 字段绑定到本轮私有定义。代理与触发区域的原关系不改写。普通机械声、警报、剧情声音不因它们属于声音而删除。
- 延续 v3 对降雨实体、已知雷电粒子、天气专用事件音及有证据的混音控制的处理。额外递归检查来源 soundscape，只删除准确资源目录中登记的雨、雷及风暴层；混合随机音效池保留其非天气成员，纯天气播放层移除。
- 不凭实体名称、目录片段或 `rain` 字样直接删除声音，例如排水、火车名字可能包含相似字样。疑似但未登记的天气资源、来源缺失音频会保留并在 `warnings` 报告。未处理的外部脚本可能动态选择其他声音，不能据静态检查声称所有天气音都已清除。

## 定义解析与隔离

从原始 BSP、实际模式 LMP 中收集声音引用。按显式资源根读取游戏 manifest 和地图专属脚本，BSP PAK 资源优先；同一根内不同内容的同路径候选拒绝。脚本中的重复定义按加载顺序选择最后一个非空定义，并记录全部来源和所选序号。重复或不支持的播放选择字段、缺失声音定义、嵌套循环、未知块结构、include/条件指令不猜测处理，而是明确报告。

纯天气定义清空后使用无音频、无 DSP 写入的空循环块占位，保留引擎对有效声音区域的切换语义，避免额外修改混响。

为可达定义生成 `lmc_src_…` 私有名称。地图专属脚本同时写入转换后 BSP 的 PAK 及 VPK；采样别名获得相同内容的对应文件，避免原内嵌脚本遮住新定义。若原图已有同路径声音脚本，原内容作为字节前缀保留，私有定义追加其后；不替换共享 `soundscapes_manifest.txt` 或全局 campaign 脚本。保留的旧定义不代表仍由转换后的环境音实体使用，检查活动映射而非全文搜索 `rain` 判断处理效果。

声音嵌入步骤仅允许该地图专属脚本的增加/追加。所有非 PAK lump、其他 PAK 资源、原脚本字节前缀均受审计；普通资源没有写回游戏安装。停用本次 VPK 后，原图资源不因这次转换被修改。不得同时启用同地图的多个转换包。

有序重复 KeyValues、随机池按值读取、最后加载定义优先、空定义不会注册的处理依据为 Valve 的 [Source SDK 声音加载实现](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/game/client/c_soundscape.cpp)。这是公开实现依据，实际 L4D2 声音效果仍须听音验收；不把 SDK 源码等同于完整 L4D2 源码。

## 检查与验收

`check` 的 `source_soundscapes` 记录来源/模式哈希、名称映射、删除的天气层、保留警告、源资源清单、活动私有脚本及打包脚本哈希。实体修改和 PAK 嵌入分别有审计。继续运行时重新验证资源并重算声音方案，防止只改 JSON 或自填哈希绕过来源约束。

游戏内应确认：非水岸地图没有新增 C5 海鸥/波浪，原环境声和室内外切换仍在，天气变化后没有不相符的雨雷声，警报/机关等事件音正常。可用 C2M1→C5M1 检查普通声音保留，再用 C4M3→C5M1 检查混合雨声剔除。机器通过与本轮听音接受分别记录，旧画面验收不自动成为新声音验收。
