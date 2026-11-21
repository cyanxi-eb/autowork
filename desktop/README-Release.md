# AutoWork Windows x64

1. 将 ZIP 完整解压到可写目录。
2. 双击 `AutoWork.exe` 打开独立工作台。
3. 点击「打开演示」，选择重复记录处理策略，查看多轮复核、返修和交付过程。

下载包内置 Python。Windows 需要 Microsoft Edge WebView2 Runtime，缺失时可从微软获取。程序与本地服务通过本机回环地址通信，项目档案和记录保存在解压目录中的 `.orchestrator/`。 [WebView2 Runtime 下载](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)

演示的 Agent 对话和派工由本地脚本模拟，CSV 验收在内存 SQLite 中执行。关闭工作台窗口后，本地服务继续处理已经开始的运行。再次打开同一个解压目录中的程序即可查看进度。

安装 Git、DSH CLI 和 Codex CLI 后，可以新建项目、点击「开始运行」，选择本地 Git 源码目录并输入验收条件。工作台以源仓库当前提交创建独立候选副本，在验收通过时保存候选提交和交付摘要。DeepSeek 协调 API 使用 `DEEPSEEK_API_KEY`。也可按已确认的验收条件直接开始。

左右栏的会话可滚动、展开和筛选。各 Agent 面板提供人设与接口配置。项目记忆、人设和交付文档可直接预览 Markdown。

随包附有 `runtime/LICENSE.txt` 及 `licenses/` 下的 WebView2 许可与声明。
