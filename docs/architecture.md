# 架构与数据保护

转换以原始 BSP 为编译输入，在副本中改变受限视觉字段，再调用 L4D2 原生 VRAD 重算光照。流程不经过 VMF、VBSP 或 VVIS，因此不重新构建几何、碰撞或可见性；输出仍须通过逐项审计，不能仅凭这一路径宣称无损。

## 管线

```text
JSON 配置 + 原图 BSP/NAV/三模式 LMP + C5 参考 + 本机资源/工具
  → check：身份、结构、风格字段和资源检查
  → build：配置/输入快照 → 视觉字段 → 官方 HDR VRAD → 保护审计
  → 官方 VPK 打包 + 解包逐字节验证 → offline_ready（旧反射）
  → prepare-capture → install-capture → 用户启动游戏/检查/采样/退出
  → collect-capture → finish-capture：来源与纹理审计 → 原名回填
  → 官方 VPK 打包 + 解包验证 → final_ready_pending_user_validation
  → remove-capture + 用户原名地图验收
```

用户截图只服务于验收。HDR cubemap 是游戏渲染的反射资源，进入最终 BSP 的数据来自原生捕获文件。

## 模块与边界

| 模块 | 职责 |
|---|---|
| `binary.py`、`entities.py`、`inspect.py` | L4D2 v21 容器、实体解析、结构与 I/O 审计 |
| `patch.py` | 原有白名单等长定点修改；不承担全局风格转换 |
| `style.py`、`profiles.py` | 明确的 C2/C6→C5 配置；唯一实体/字段、已知值、玩法输出保护和重复应用检查 |
| `configuration.py` | JSON 配置、路径、源图身份、输入与工具哈希 |
| `native.py` | 不经过 shell 的官方工具执行、超时日志、光照保护合同、VPK 解包验证 |
| `reflections.py` | 纯字节采样交接、guard 控制文件、HDR 来源/格式审计 |
| `handoff.py` | 用户主动调用的临时安装、收据、游戏退出检查、归档及精确清理 |
| `workflow.py` | 阶段编排、`run.json`、失败信息和产物追踪 |

## 保护合同

风格阶段仅支持已知地图和实体形状；未知、重复或歧义目标会拒绝处理。基础 BSP 与三份模式实体 LMP 一起迁移，避免有效模式补丁重新覆盖风格。NAV 及可选 exclude 文件保持原字节。C2 使用已有受限配置；C6 使用独立角色映射，保留原风暴/雨、闪电事件、触发器、实体位置和室内 fog volume 路由。允许的视觉 I/O 参数修改有明确匹配条件，不能概括为全部 I/O 均不变。

VRAD 后，实体块与非许可 lump 内容必须保持。光照相关 lump 也有内部约束：HDR face 只能改变已知光照字段，静态模型 `sprp` payload 不变，detail prop 保留字典和布置，PAK 只允许识别出的原生 HDR 静态模型光照流变化。VHV 的拓扑、校验和、记录范围与填充接受结构检查。容器偏移可以随重写改变，不以整文件字节相同作为烘焙验收条件。

反射导入检查本次基线和唯一别名、未改动的非 PAK 数据、原有 PAK 资源、采样坐标集合、支持的 HDR VTF 布局和有限 RGB 值，并记录各采样纹理是否发生字节变化；同字节数量会单列，不能仅凭纹理变化与否认定画面改善。当前只接收已经观察到的有限 VTF 变体；不支持的格式拒绝导入。原生捕获的 alpha 特性不能当作颜色质量证明。只回填采样 HDR 反射，原 LDR 与默认反射保留。

BSPZIP 完成后再次核对除 PAK 外的内容及逐资源替换集合；VPK 完成后用官方工具解包，核对每个预期文件的字节。工具退出 0、`CAPTURE_REQUESTED` 或完成提示，都不能替代这些检查。

## 运行与恢复

`run.json` 记录本机配置、输入清单、哈希、原生命令、阶段状态、审计和产物。后续操作检查输入及追踪产物是否变化；配置文件本身也属于输入。记录覆盖明确输入、工具及部分运行依赖，不是整个游戏资源安装的完整不可变快照。运行期间应保持安装资源不变，挂载优先级仍需实机确认。

输出目录与游戏/工具安装分离，构建拒绝已存在的输出目录。失败阶段保留日志与产物；本版没有自动续跑烘焙或通用缓存恢复。采样可归档多次 evidence attempt，导入/打包失败也可在编号 attempt 中重试，但捕获必须来自本次基线和别名。

只有显式 `install-capture`/`remove-capture` 操作临时安装。清理根据安装收据校验精确目标，不要求旧输入仍存在。发布 VPK 由用户退出游戏后手动部署。临时别名解决采样隔离问题，不能代替原名地图的玩法验收。

## 原理依据

Valve 公开 VRAD 源码直接读取 BSP，计算光照并写回产物，支持“无需恢复 VMF 即可研究重烘焙”的架构依据；具体兼容性由本项目对 L4D2 官方工具和实图的审计确定。[Valve VRAD](https://github.com/ValveSoftware/source-sdk-2013/blob/b8cfb12c0e083a2ef5b2f9f9b50f3902fa034474/src/utils/vrad/vrad.cpp)

Valve 的默认 cubemap 实现将默认纹理与在引擎内生成场景反射区分。因此离线 VRAD 输出不能自动宣称已有新场景反射。[Valve cubemap 实现](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/utils/vbsp/cubemap.cpp)

Source SDK 2013 不是 L4D2 引擎源码或其原生工具替代品。公开源码用于说明结构，L4D2 的格式和行为以本机原生工具产物和实际游戏测试为依据。[Valve SDK 仓库](https://github.com/ValveSoftware/source-sdk-2013)

## 尚未覆盖

任意地图自动角色识别、自定义地图、无引擎等效反射渲染、全部材质依赖验证、无人值守游戏启动、GUI/EXE 和 S0—S7 阶段演示均未实现。静态保护证据不包含完整游戏流程、所有画质/模式或联机兼容验收；C6 特别需要用户观察保留的动态天气对视觉和捕获的影响。
