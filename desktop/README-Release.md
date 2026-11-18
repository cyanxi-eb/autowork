# AutoWork Windows x64 演示版

1. 将 ZIP 完整解压到可写目录。
2. 双击 `AutoWork.exe` 打开独立工作台。
3. 点击「打开演示」，选择重复记录处理策略，查看需求、审核、返修和交付过程。

下载包内置 Python。Windows 需要 Microsoft Edge WebView2 Runtime，缺失时可从微软获取。程序与本地服务通过本机回环地址通信，项目档案和记录保存在解压目录中的 `.orchestrator/`。 [WebView2 Runtime 下载](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)

此版本的 Agent 对话和派工由本地脚本模拟，CSV 验收在内存 SQLite 中执行。关闭工作台窗口后，当前演示可以继续运行。再次打开同一个解压目录中的程序即可继续查看。

随包附有 `runtime/LICENSE.txt` 及 `licenses/` 下的 WebView2 许可与声明。
