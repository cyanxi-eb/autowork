using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr window, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr window);

    [STAThread]
    private static void Main()
    {
        string build = AppDomain.CurrentDomain.BaseDirectory;
        bool portable = File.Exists(Path.Combine(build, "runtime", "python.exe"));
        string instanceName = @"Local\AutoWork.Workbench";
        if (portable)
        {
            using (var hash = SHA256.Create())
                instanceName += "." + BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(build.ToUpperInvariant())));
        }
        using (var instance = new Mutex(false, instanceName))
        {
            try
            {
                if (!instance.WaitOne(0))
                {
                    ActivateExistingWindow();
                    return;
                }
            }
            catch (AbandonedMutexException) { }

            try
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                using (var window = new WorkbenchWindow(build, portable))
                {
                    Application.Run(window);
                }
            }
            finally
            {
                instance.ReleaseMutex();
            }
        }
    }

    private static void ActivateExistingWindow()
    {
        using (var current = Process.GetCurrentProcess())
        {
            foreach (var process in Process.GetProcessesByName(current.ProcessName))
            {
                using (process)
                {
                    if (process.Id == current.Id || process.SessionId != current.SessionId)
                        continue;
                    try
                    {
                        if (!String.Equals(process.MainModule.FileName, Application.ExecutablePath, StringComparison.OrdinalIgnoreCase))
                            continue;
                    }
                    catch (System.ComponentModel.Win32Exception) { continue; }
                    catch (InvalidOperationException) { continue; }
                    IntPtr window = process.MainWindowHandle;
                    if (window == IntPtr.Zero)
                        continue;
                    if (IsIconic(window))
                        ShowWindowAsync(window, 9); // SW_RESTORE
                    SetForegroundWindow(window);
                    return;
                }
            }
        }
    }
}

internal sealed class WorkbenchWindow : Form
{
    private static readonly Uri DevelopmentUrl = new Uri("http://127.0.0.1:8765/");
    private readonly string build;
    private readonly bool portable;
    private readonly Label status;
    private readonly WebView2 view;

    public WorkbenchWindow(string build, bool portable)
    {
        this.build = build;
        this.portable = portable;
        Text = "AutoWork · 项目工作台";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        ClientSize = new Size(1280, 820);
        MinimumSize = new Size(520, 460);
        StartPosition = FormStartPosition.CenterScreen;
        WindowState = FormWindowState.Maximized;
        BackColor = Color.FromArgb(238, 242, 248);

        status = new Label {
            Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter,
            Font = new Font("Microsoft YaHei UI", 11), Text = "正在连接 AutoWork…"
        };
        view = new WebView2 { Dock = DockStyle.Fill, Visible = false };
        Controls.Add(status);
        Controls.Add(view);
        Shown += StartWorkbench;
    }

    private async void StartWorkbench(object sender, EventArgs args)
    {
        try
        {
            Uri workbench = await EnsureBackend();
            if (IsDisposed || Disposing)
                return;

            string profile = portable ? Path.Combine(build, ".orchestrator", "profile") : Path.Combine(build, "profile");
            var environment = await CoreWebView2Environment.CreateAsync(null, profile);
            if (IsDisposed || Disposing)
                return;
            await view.EnsureCoreWebView2Async(environment);
            if (IsDisposed || Disposing)
                return;

            var browser = view.CoreWebView2;
            browser.Settings.IsStatusBarEnabled = false;
            browser.Settings.AreDevToolsEnabled = false;
            browser.Settings.AreBrowserAcceleratorKeysEnabled = false;
            browser.NavigationStarting += (s, e) => {
                Uri target;
                e.Cancel = !Uri.TryCreate(e.Uri, UriKind.Absolute, out target) ||
                    target.Scheme != workbench.Scheme || target.Host != workbench.Host || target.Port != workbench.Port;
            };
            browser.NewWindowRequested += (s, e) => e.Handled = true;
            view.Visible = true;
            status.Visible = false;
            browser.Navigate(workbench.AbsoluteUri);
        }
        catch (Exception error)
        {
            if (!IsDisposed && !Disposing)
                status.Text = "AutoWork 启动失败\r\n\r\n" + error.Message;
        }
    }

    private async Task<Uri> EnsureBackend()
    {
        using (var http = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false }))
        {
            http.Timeout = TimeSpan.FromSeconds(2);
            Uri connected = await FindWorkbench(http);
            if (connected != null)
                return connected;

            string python = portable ? Path.Combine(build, "runtime", "python.exe") :
                File.ReadAllText(Path.Combine(build, "python-path.txt")).Trim();
            string project = portable ? build : Path.GetFullPath(Path.Combine(build, "..", ".."));
            using (var child = Process.Start(new ProcessStartInfo {
                FileName = python, Arguments = portable ? "-B -m orchestrator --port 0 --desktop" : "-m orchestrator",
                WorkingDirectory = project,
                UseShellExecute = false, CreateNoWindow = true
            }))
            {
                for (int attempt = 0; attempt < 32; attempt++)
                {
                    connected = await FindWorkbench(http);
                    if (connected != null)
                        return connected;
                    if (child.HasExited)
                        throw new InvalidOperationException("本地服务提前退出。请确认文件已经完整解压到可写目录。");
                    await Task.Delay(250);
                }
            }
            throw new TimeoutException("等待本地服务启动超时。可关闭此窗口后重试。");
        }
    }

    private async Task<Uri> FindWorkbench(HttpClient http)
    {
        Uri url = DevelopmentUrl;
        string instance = null;
        if (portable)
        {
            string file = Path.Combine(build, ".orchestrator", "desktop-endpoint.json");
            if (!File.Exists(file))
                return null;
            try
            {
                var endpoint = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(file));
                object port;
                object key;
                object root;
                if (endpoint == null || !endpoint.TryGetValue("port", out port) || !(port is int) ||
                    (int)port < 1 || (int)port > 65535 || !endpoint.TryGetValue("instance", out key) || !(key is string) ||
                    !endpoint.TryGetValue("root", out root) ||
                    !String.Equals(root as string, build.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
                    return null;
                instance = (string)key;
                url = new Uri("http://127.0.0.1:" + port + "/");
            }
            catch (IOException) { return null; }
            catch (ArgumentException) { return null; }
        }
        return await HasWorkbench(http, url, instance) ? url : null;
    }

    private static async Task<bool> HasWorkbench(HttpClient http, Uri url, string instance)
    {
        HttpResponseMessage response;
        try
        {
            response = await http.GetAsync(new Uri(url, "api/workbench"));
        }
        catch (HttpRequestException)
        {
            return false;
        }
        catch (TaskCanceledException)
        {
            if (instance != null)
                return false;
            throw;
        }
        using (response)
        {
            if (response.IsSuccessStatusCode && response.Content.Headers.ContentType != null &&
                response.Content.Headers.ContentType.MediaType == "application/json")
            {
                try
                {
                    var payload = new JavaScriptSerializer().DeserializeObject(await response.Content.ReadAsStringAsync())
                        as Dictionary<string, object>;
                    if (payload != null && payload.ContainsKey("agents") && payload.ContainsKey("projects") && payload.ContainsKey("demo"))
                    {
                        object value;
                        return instance == null || (payload.TryGetValue("desktop_instance", out value) && instance.Equals(value));
                    }
                }
                catch (ArgumentException) { }
            }
        }
        if (instance != null)
            return false;
        throw new InvalidOperationException("端口 8765 已被其他服务占用。");
    }
}
