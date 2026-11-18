# 项目工作台 · 骨架

在工作台创建项目，记录初始需求和后续补充。中间是 DeepSeek 项目对话，左右两侧可独立切换 Agent、跟随最新会话或固定历史轮次。首次启动可以用「打开示例」查看 DSH 的两次交付与 Codex 的复核记录。

## 本机运行

在此目录下用 Python 3.10 或更新版本启动。运行只依赖 Python 标准库。

```powershell
Set-Location -LiteralPath 'D:\codex-sp\agent-project-orchestrator'
python -m orchestrator
```

打开 `http://127.0.0.1:8765`。服务只绑定本机回环地址。可用 `--port` 相应调整端口，用 `--data-dir` 指定单独的数据目录。

当前工作台使用固定示例和本地模拟应答。新项目处于「需求收集」，示例处于「模拟复核」。页面上能看到项目状态和每轮交付版本。浏览器使用增量事件游标恢复记录，左右布局保存在本机浏览器中。

## 目录与接口

| 位置 | 职责 |
| --- | --- |
| `orchestrator/__main__.py` | 本机 HTTP 入口、项目和事件接口 |
| `orchestrator/workbench.py` | 建档、对话接收、示例数据 |
| `orchestrator/store.py` | SQLite 状态、顺序事件、需求 Markdown 导出 |
| `orchestrator/agents.py` | Agent 描述、任务与适配器入口，模拟协调器 |
| `orchestrator/web/` | 三栏工作台 |
| `.orchestrator/` | 默认运行数据。SQLite 保存对话和事件，每个项目有独立档案目录 |

`GET /api/workbench` 读取模式、Agent 和项目列表。`POST /api/projects` 建档，`POST /api/projects/{id}/messages` 收集补充，`GET /api/projects/{id}/events?after=0` 按当前项目顺序返回事件及下一游标。示例用 `POST /api/demo` 打开。

`requirements/需求档案.md` 由程序从用户对话生成，适合交接和查看。SQLite 提交后更新档案，导出中断时可在下次写入或启动时重建。项目目录同时预留勘察、规划、阶段、审核、证据和工作副本的位置。`progress.md` 目前只初始化，正式验收之后才追加完成记录。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试使用临时目录，覆盖建档、续写、增量读取、重启恢复、项目隔离和入口校验。

已在 Chrome 验证桌面三栏与窄屏抽屉，以及新建项目、发送补充、两侧独立切换、刷新恢复和断线重连。

真实 DeepSeek API 和 DSH、Codex Runner 接入，模型与思考档位发现，以及派工、返修、验收状态机属于后续阶段。首个接入验收见 [总体方案](自动化项目推进-总体方案.md) 的 P0。
