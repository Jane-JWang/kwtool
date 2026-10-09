# kwtool

Windows 桌面网络核查框架。左侧是批次控制面板，右侧使用可见的 Google Chrome。

## 运行

要求 Python 3.11+ 和已安装的 Google Chrome。建议在 `kwtool` 目录内创建虚拟环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
python launcher.py
```

演示时，在左侧每行输入一个主体，选择“本地演示”，点击“查询”。程序自动打印 PDF 并处理下一行。“停止”会在当前浏览器操作结束后终止批次。界面显示进度和状态，详细日志写入 `%APPDATA%\kwtool\kwtool.log`。需要排查网站变化时，可勾选“诊断模式”；适配器调用 `context.capture_diagnostic(label, page)` 后，截图和页面文字、元素结构会保存到 `%APPDATA%\kwtool\diagnostics\`。诊断文件不保存网址查询参数的值，可能包含网页上显示的主体和查询结果，请按需分享。

选择“企信网”后，程序会在右侧 Chrome 搜索每家企业、依次加载详情栏目、保存网页 PDF，并通过网站发送邮件报告。首次访问会等待网站的环境检查；页面出现登录入口时，会提示用户在 Chrome 中登录。搜索和发送报告时出现的验证码由用户手动完成。搜索没有唯一完全匹配项时，在右侧网页点击正确的企业。PDF 已保存但邮件未获网站成功提示时，该行记为失败并继续下一家，已保存的 PDF 不会删除或自动重发邮件。企信网初始打印设置为 A4 纵向、55% 缩放、打印背景，可在“网页专属配置”中调整。

默认输出模板为 `Output/[date]/[id]-[object]-[website].pdf`。相对路径基于用户“文档/kwtool”；支持 `[date]`（YYMMDD）、`[time]`（HHMMSS）、`[year]`、`[month]`、`[day]`、`[hour]`、`[minute]`、`[second]`、`[id]`（批次序号）、`[object]`、`[website]`。绝对路径也可使用。已有文件会自动增加 `_2`、`_3` 等后缀。Chrome 专用资料保存在 `%LOCALAPPDATA%\kwtool\chrome-profile\cdp`，界面设置保存在 `%APPDATA%\kwtool\settings.json`。程序默认自行启动独立 Chrome，再通过本机调试端口连接。

如需单独验证 Chrome 的启动和连接，可以手动启动独立 Chrome，再让 kwtool 连接。先关闭 kwtool，在 PowerShell 中执行以下命令，并保持 Chrome 窗口打开：

```powershell
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
$profile = Join-Path $env:TEMP ("kwtool-cdp-" + [guid]::NewGuid().ToString("N"))
& $chrome --user-data-dir=$profile --remote-debugging-address=127.0.0.1 --remote-debugging-port=9229 https://www.gsxt.gov.cn
```

在这个 Chrome 中确认网页可访问后，另开一个 PowerShell 窗口启动 kwtool：

```powershell
cd D:\Projects\prj_kwtool\kwtool
$env:KWTOOL_CDP_URL = 'http://127.0.0.1:9229'
.\.venv\Scripts\python.exe launcher.py
```

连接模式只接受本机调试地址；关闭 kwtool 不会关闭手动启动的 Chrome。此模式使用独立的临时资料夹，不读取日常 Chrome 资料。

## 新增网站

在 `kwtool/adapters/` 新建 `SiteAdapter` 子类，实现 `run_one(context, subject)`，并在 `kwtool/adapters/__init__.py` 的 `registered_adapters()` 中注册。适配器通过 `settings_schema` 声明网站专属设置；打印设置可能因网站而异，应由对应适配器处理。`context.page` 用于浏览器操作，`context.wait_until(ready, message)` 可根据网站状态检测验证码完成后自动继续，`context.save_pdf()` 和 `context.save_download(download)` 用于保存结果。`kwtool.diagnostics.browser_snapshot()` 可记录脱敏后的标签页状态，`context.capture_diagnostic(label, page)` 可在诊断模式保存页面证据。长流程应定期调用 `context.check_stop()`。

## 测试和打包

```powershell
python -m pytest
.\build.ps1
```

`build.ps1` 生成 `dist/kwtool/kwtool.exe` 及其依赖文件。它是可分发的目录版 EXE，运行设备仍需安装 Google Chrome；安装器将在后续阶段制作。打包后应在允许启动 Chrome 的 Windows 桌面环境中完成演示流程的端到端检查。
