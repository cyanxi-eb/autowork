# AutoWork · 项目工作台

中间是项目对话，左右两侧可查看 Agent 的任务和历史轮次。普通项目可在本地 Git 仓库的独立候选副本中运行「需求确认 → 勘察 → 规划 → 实施 → 复核」。CSV 演示仍可离线运行。

## Windows 桌面版

从 [GitHub Releases](https://github.com/cyanxi-eb/autowork/releases) 下载 Windows x64 ZIP，完整解压后双击 `AutoWork.exe`。下载包内置 Python，项目数据保存在解压目录中。Windows 需要 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)。

从桌面双击 **AutoWork** 打开独立窗口。应用使用独立的任务栏图标，默认最大化，重复打开快捷方式会切回现有窗口。启动时自动连接或启动本地工作台，关闭窗口后本地服务继续推进已经开始的演示。

首次在 Windows x64 上安装桌面入口时，在项目目录运行以下命令。需要 Python 3.10 或更新版本、.NET Framework 和 WebView2 Runtime。安装脚本从 NuGet 获取固定版本的 WebView2 SDK，并生成桌面快捷方式。

```powershell
.\desktop\Install.ps1 -Open
```

从源码安装的桌面入口与 `8765` 网页共用项目数据，窗口偏好各自保存在对应的应用或浏览器中。桌面构建文件及 WebView2 配置位于 `.orchestrator/desktop/`。

从源码生成发布 ZIP 时运行 `.\desktop\New-Release.ps1 -Version v0.1.1`，产物位于 `.orchestrator/releases/`。

## 本机运行

在此目录下用 Python 3.10 或更新版本启动。运行只依赖 Python 标准库。

```powershell
python -m orchestrator
```

打开 `http://127.0.0.1:8765`。服务只绑定本机回环地址。可用 `--port` 相应调整端口，用 `--data-dir` 指定单独的数据目录。

## 让 AutoWork 优化自己的代码

1. 在 Git 源码目录启动工作台。安装 Git、DeepSeek Harness CLI 和 Codex CLI，并确认 CLI 已使用自己的账户登录。工作台先从 PATH 寻找命令，在 Windows 也查找常见桌面安装位置。可在 Agent 的「接口」中指定实际命令路径。
2. 新建项目，填写一个小范围优化。点击「开始运行」，选中 AutoWork 源码目录，填写验收条件和 Codex 可以独立执行的检查。源码启动时自动填入当前仓库。使用 `DEEPSEEK_API_KEY` 环境变量接入 DeepSeek。也可以勾选按验收条件直接开始，由工作台跳过 API 追问。
3. DeepSeek 如需补充，会暂停在中央对话等待用户。确认后，DSH 只读勘察，Codex 只读规划，DSH 在独立副本实施。工作台记录候选提交，Codex 在同一固定提交上只读复核。复核要求修改时，工作台自动派返修，沿用原写入会话和复核会话，最多返修三次。
4. 点击「交付物」查看各轮 Markdown、验收记录和候选副本。原仓库以启动时的提交为基线，工作区的未提交更改不参与运行。候选副本位于数据目录的 `runs/<运行ID>/checkout`，由用户审阅后决定如何合并。

DSH 在默认自动配置下，有 `DEEPSEEK_API_KEY` 时使用 headless profile 的模型设置，未设该变量时通过 DSH 的本机凭据机制使用已登录的桌面账户。也可在「接口」中指定 Provider、模型和思考档位。覆盖只应用于当前 CLI 调用。DeepSeek 项目协调 API 的密钥设置独立于 DSH 登录。

运行数据保存在 SQLite 中。阶段之间可在重启后续接。执行期间发生中断时，工作台保留候选副本并显示「执行待核实」。中断发生在独立复核时，可以针对原候选提交重试只读复核。Agent 的一个工作轮次有独立任务号，最终回复统一使用 JSON v1，必须回填任务号和候选提交。工作台会检查协议和候选状态，业务验收依据仍来自 Codex 的复核。

## 怎么演示

1. 点击「打开演示」。DeepSeek 会询问 CSV 重复编号应该保留原记录还是更新原记录。两个选项均可走完流程，最终 A01 的名称会随所选策略改变。
2. 选定后观察顶部进度和版本。`rev-01` 在内存 SQLite 测试中产生重复行，`rev-02` 修复行数但仍选错同编号处理策略。`rev-03` 通过复核后，系统自动进入第二阶段并生成交付摘要。每次不通过都会进入下一轮返修，达到三次返修上限仍未通过时阻塞交付。
3. 用「暂停」和「继续」控制进度。刷新可接回当前轮次，重启服务后可从 SQLite 中保存的步骤接续。演示完成后用「再跑一次」建立独立的新轮次，旧结果可从项目列表打开。
4. 打开「交付物」查看需求、方案、历轮复核与交付摘要。两侧默认展示全部会话，点击卡片展开派工和回复。也可以选择某轮作对照，例如左侧看第二次返修，右侧看第二轮复核。
5. 顶栏可切换亮色和暗色。点击 Agent 标题栏的「API 接口」或「CLI 接口」，可查看和保存接入配置。DeepSeek 配置仅保存密钥环境变量名。演示始终使用本地模拟。

演示的对话和派工由本地脚本模拟，各候选的 CSV 验收实际运行在内存 SQLite 中。

## 人设与项目记忆

三个 Agent 的面板都有「人设」入口。Markdown 人设分别描述 [DeepSeek 的协调与派工](orchestrator/personas/deepseek/SOUL.md)、[DSH 的勘察与交付](orchestrator/personas/dsh/SOUL.md)和 [Codex 的规划与复核](orchestrator/personas/codex/SOUL.md)。人设与记忆分开维护的结构参考了 [dsh-soul-md](https://github.com/Scorp1o117/dsh-soul-md)。

「项目记忆」位于项目切换栏。默认数据目录下，每个项目使用 `.orchestrator/projects/<项目ID>/MEMORY.md`。新项目建档和旧项目升级启动时建立该文件，运行后刷新其中的自动续接区。现场约束、补充决定和交接说明可以写在自动区之外。自动区记录初始需求摘录、确认过的策略、当前轮次、最近复核和交付链接。原始资料仍从项目档案查阅。

任务按角色读取人设，并按项目编号读取对应 `MEMORY.md`。内容过长时对任务快照限长，保留首尾。人设、项目记忆和交付物均可在工作台内预览 Markdown 和查看源文。

## 目录与接口

| 位置 | 职责 |
| --- | --- |
| `orchestrator/__main__.py` | 本机 HTTP 入口、项目和事件接口 |
| `orchestrator/workbench.py` | 项目建档和启动前的需求记录 |
| `orchestrator/demo.py` | 演示状态推进、内存 SQLite 验收及阶段放行 |
| `orchestrator/live_runs.py` | 真实任务派发、进度、复核与返修 |
| `orchestrator/agent_protocol.py`、`orchestrator/agent_runtimes.py` | JSON 任务协议与 DeepSeek、DSH、Codex 接入 |
| `orchestrator/candidate.py` | 独立 Git 候选副本和提交 |
| `orchestrator/store.py` | SQLite 状态、顺序事件、需求 Markdown 导出 |
| `orchestrator/agents.py` | Agent 描述、任务与适配器入口，模拟协调器 |
| `orchestrator/web/` | 三栏工作台 |
| `.orchestrator/` | 默认运行数据。SQLite 保存对话和事件，每个项目有独立档案目录 |

`GET /api/workbench` 读取模式、Agent、项目列表及演示配置。`POST /api/projects` 建档，`POST /api/projects/{id}/messages` 收集普通项目补充，`GET /api/projects/{id}/events?after=0` 按当前项目顺序返回事件及下一游标。演示用 `POST /api/demo` 打开，请求 `{"fresh": true}` 创建新轮次。`POST /api/projects/{id}/demo` 接受 `confirm`、`pause`、`resume` 三个动作。确认时提供 `keep` 或 `update` 策略。

`GET` 和 `POST /api/agents/{id}/connection` 读取和保存工作台内的 Agent 接入配置。

`POST /api/projects/{id}/run` 启动真实运行、结束等待补充的运行或重试中断的复核。`GET /api/agents/{id}/persona` 查看人设，`GET /api/projects/{id}/memory` 查看项目记忆，`GET /api/projects/{id}/document?path=...` 读取项目档案中的文档。

`requirements/需求档案.md` 由程序从用户对话生成。SQLite 提交步骤、对话和证据后更新 Markdown，导出中断时可在下次写入或启动时重建。`progress.md` 只在演示阶段实际通过后记录对应时间和候选版本。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试使用临时目录，覆盖 CSV 演示、普通项目、接口配置、JSON 协议、会话复用、候选隔离、返修放行和中断防重发。真实流程测试使用 Agent 夹具。

已在 Chrome 验证桌面与窄屏上的两种演示策略、暂停后刷新再继续、历史审核固定、交付物查看与新轮次重跑。

更完整的演进路线见 [总体方案](自动化项目推进-总体方案.md)。
