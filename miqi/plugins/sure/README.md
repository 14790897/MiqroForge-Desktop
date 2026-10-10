# SURE 插件(#1267)

把[SURE](https://github.com/lichman0405/SURE)项目核查引擎接入 MiQroForge 的**内置插件**。
随应用分发(system 插件目录 `miqi/plugins/`,打包时由 `miqi.spec` 的 `datas` 打进桥);

## 提供什么

| 组件 | 内容 |
|---|---|
| MCP 服务声明 | `sure`(stdio,`sure mcp serve`);`tool_timeout` 600s、进度心跳 10s。会话启动时连接,5 个工具以 `mcp_sure_*` 注册进工具表 |
| 快捷命令 | `/sure-check <项目绝对路径>`——把核查纪律(显式绝对路径、如实转述、修复走 recheck)注入当轮系统提示 |
| 技能 | **不重复携带**:使用内置技能 `sure-check` / `sure-fix`(随应用分发,#1262) |

## 二进制解析约定(实现前定题,记录取舍)

插件清单是静态声明,无法在此渲染本机绝对路径,因此本插件声明 `command: "sure"`,
由运行时的插件 MCP 接线(resolve_plugin_command)按以下顺序解析:

1. 绝对路径(存在即用);
2. `PATH`(`shutil.which`);
3. **Windows 每用户安装约定** `%LOCALAPPDATA%\SURE\bin\sure.exe`——SURE 官方安装器
   的落地位置,与 SURE 自身启动器的解析顺序一致。

三条都未命中 → **跳过该服务器并记日志**(fail-visible:宁可不注册工具让 Agent
如实说「SURE 未安装」,也不注入一个连不上的服务器)。便携/自定义安装位置请改用
用户级显式配置(`~/.forge/config.json` 的 `tools.mcpServers.sure` 写绝对路径)——
显式配置与本插件的声明**同名时以显式配置为准**。

> 备选方案(未采用):插件安装器在安装时渲染绝对路径——本插件是**内置分发**,
> 没有安装器步骤可承载渲染;PATH + 每用户约定的解析覆盖了官方安装器的两条落地
> 路径,已在运行时统一实现并测试。

## 已知边界

- 启停状态为内存态(PluginManager 既有语义),重启后恢复;同名服务器以显式配置为准;
- 快捷命令由 `task_runner` 在消息以 `/` 开头时拦截注入(插件命令的既有机制);
- 与「检测到本机 SURE 时预置默认条目」(#1268)并存时互不冲突:两者声明同一
  `sure` 服务器,合并规则下后到者不覆盖,连接只发生一次。
