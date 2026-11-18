# 项目工作台 · 本地 Demo

中间是 DeepSeek 项目对话，左右两侧可独立切换 Agent、跟随最新会话或固定历史轮次。点击「打开演示」体验需求确认、勘察、规划、实施、审核、返修和自动交付的完整流程。

## Windows 桌面版

从 [GitHub Releases](https://github.com/cyanxi-eb/autowork/releases) 下载 Windows x64 ZIP，完整解压后双击 `AutoWork.exe`。下载包内置 Python，项目数据保存在解压目录中。Windows 需要 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)。

从桌面双击 **AutoWork** 打开独立窗口。应用使用独立的任务栏图标，默认最大化，重复打开快捷方式会切回现有窗口。启动时自动连接或启动本地工作台，关闭窗口后本地服务继续推进已经开始的演示。

首次在 Windows x64 上安装桌面入口时，在项目目录运行以下命令。需要 Python 3.10 或更新版本、.NET Framework 和 WebView2 Runtime。安装脚本从 NuGet 获取固定版本的 WebView2 SDK，并生成桌面快捷方式。

```powershell
.\desktop\Install.ps1 -Open
```

从源码安装的桌面入口与 `8765` 网页共用项目数据，窗口偏好各自保存在对应的应用或浏览器中。桌面构建文件及 WebView2 配置位于 `.orchestrator/desktop/`。

从源码生成发布 ZIP 时运行 `.\desktop\New-Release.ps1 -Version v0.1.0`，产物位于 `.orchestrator/releases/`。

## 本机运行

在此目录下用 Python 3.10 或更新版本启动。运行只依赖 Python 标准库。

```powershell
python -m orchestrator
```

打开 `http://127.0.0.1:8765`。服务只绑定本机回环地址。可用 `--port` 相应调整端口，用 `--data-dir` 指定单独的数据目录。

## 怎么演示

1. 点击「打开演示」。DeepSeek 会询问 CSV 重复编号应该保留原记录还是更新原记录。两个选项均可走完流程，最终 A01 的名称会随所选策略改变。
2. 选定后观察顶部八步进度。DSH 提交 `rev-01`，初版在实际内存 SQLite 测试中重复导入后从 2 行增至 4 行。Codex 报告问题 F-01，DSH 提交 `rev-02`。复验通过后，系统自动进入第二阶段并生成交付摘要。
3. 用「暂停」和「继续」控制进度。刷新可接回当前轮次，重启服务后可从 SQLite 中保存的步骤接续。演示完成后用「再跑一次」建立独立的新轮次，旧结果可从项目列表打开。
4. 打开「交付物」查看需求定稿、勘察记录、方案、阶段文档、两轮复核与交付摘要。右侧可以固定初版审核，左侧跟随最新 DSH 交付。

对话和派工由本地脚本模拟，两个候选的 CSV 验收实际运行在内存 SQLite 中。普通「新建项目」可收集自己的需求，暂时停在需求收集阶段。

## 目录与接口

| 位置 | 职责 |
| --- | --- |
| `orchestrator/__main__.py` | 本机 HTTP 入口、项目和事件接口 |
| `orchestrator/workbench.py` | 普通项目建档与对话接收 |
| `orchestrator/demo.py` | 演示状态推进、内存 SQLite 验收及阶段放行 |
| `orchestrator/store.py` | SQLite 状态、顺序事件、需求 Markdown 导出 |
| `orchestrator/agents.py` | Agent 描述、任务与适配器入口，模拟协调器 |
| `orchestrator/web/` | 三栏工作台 |
| `.orchestrator/` | 默认运行数据。SQLite 保存对话和事件，每个项目有独立档案目录 |

`GET /api/workbench` 读取模式、Agent、项目列表及演示配置。`POST /api/projects` 建档，`POST /api/projects/{id}/messages` 收集普通项目补充，`GET /api/projects/{id}/events?after=0` 按当前项目顺序返回事件及下一游标。演示用 `POST /api/demo` 打开，请求 `{"fresh": true}` 创建新轮次。`POST /api/projects/{id}/demo` 接受 `confirm`、`pause`、`resume` 三个动作。确认时提供 `keep` 或 `update` 策略。

`requirements/需求档案.md` 由程序从用户对话生成。SQLite 提交步骤、对话和证据后更新 Markdown，导出中断时可在下次写入或启动时重建。`progress.md` 只在演示阶段实际通过后记录对应时间和候选版本。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试使用临时目录，覆盖两种策略、初版失败、返修失败时阻塞交付、暂停与重启恢复，以及普通项目建档、续写、增量读取、隔离和入口校验。

已在 Chrome 验证桌面与窄屏上的两种演示策略、暂停后刷新再继续、历史审核固定、交付物查看与新轮次重跑。

真实 DeepSeek API 和 DSH、Codex Runner 接入，模型与思考档位发现，以及派工、返修、验收状态机属于后续阶段。首个接入验收见 [总体方案](自动化项目推进-总体方案.md) 的 P0。
