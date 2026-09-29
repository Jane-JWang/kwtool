# kwtool

Windows 桌面网络核查框架。左侧是批次控制面板，右侧使用可见的 Google Chrome。当前仅提供本地演示网站，不访问或核查任何真实网站。

## 运行

要求 Python 3.11+ 和已安装的 Google Chrome。建议在 `kwtool` 目录内创建虚拟环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
python launcher.py
```

演示时，在左侧每行输入一个主体，选择“本地演示”，点击“查询”。右侧出现演示页后点击左侧“继续”，程序会打印 PDF 并处理下一行。“停止”会在当前浏览器操作结束后终止批次。

默认输出模板为 `Output/[date]/[id]-[object]-[website].pdf`。相对路径基于用户“文档/kwtool”；`[date]` 为本地日期 `YYMMDD`，`[id]` 为从 `001` 开始的行序号。绝对路径也可使用。已有文件会自动增加 `_2`、`_3` 等后缀。Chrome 专用资料保存在 `%LOCALAPPDATA%\kwtool\chrome-profile`，界面设置保存在 `%APPDATA%\kwtool\settings.json`。不要用日常 Chrome 的资料目录替换专用目录。

## 新增网站

在 `kwtool/adapters/` 新建 `SiteAdapter` 子类，实现 `run_one(context, subject)`，并在 `kwtool/adapters/__init__.py` 的 `registered_adapters()` 中注册。适配器通过 `settings_schema` 声明网站专属设置；`context.page` 用于浏览器操作，`context.wait_for_user(message)` 用于人工完成验证码，`context.save_pdf()` 和 `context.save_download(download)` 用于保存结果。长流程应定期调用 `context.check_stop()`。界面与批次执行器无需为新网站改代码。

## 测试和打包

```powershell
python -m pytest
.\build.ps1
```

`build.ps1` 生成 `dist/kwtool/kwtool.exe` 及其依赖文件。它是可分发的目录版 EXE，运行设备仍需安装 Google Chrome；安装器将在后续阶段制作。打包后应在允许启动 Chrome 的 Windows 桌面环境中完成演示流程的端到端检查。
