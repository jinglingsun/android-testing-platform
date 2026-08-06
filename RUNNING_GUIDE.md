# VS Code 快速运行教程

这份教程分成两部分：

- 前期环境准备：只需要做一次。
- 运行项目：以后每次实验都按这个流程启动。

## 一、前期环境准备

### 1. 安装并验证 Python

先安装 Python 3.10 或更高版本。

安装时建议勾选：

```text
Add python.exe to PATH
```

安装完成后，重新打开 VS Code 终端，执行：

```powershell
python --version
```

如果能看到 Python 版本号，说明 Python 环境可用。

### 2. 安装 Android Studio

安装 Android Studio，并通过 Android Studio 安装 Android SDK。

需要确保安装了：

- Android SDK Platform-Tools
- Android SDK Build-Tools

Platform-Tools 里有 `adb`，用于连接手机或模拟器。

Build-Tools 里有 `aapt2`，用于解析 APK 包名。

### 3. 配置 Android SDK 工具到环境变量 PATH

把 Android SDK 的下面两个目录加入 Windows 的 `Path` 环境变量。

Platform-Tools：

```text
<Android SDK 路径>\platform-tools
```

Build-Tools：

```text
<Android SDK 路径>\build-tools\<版本号>
```

常见示例：

```text
C:\Users\<你的用户名>\AppData\Local\Android\Sdk\platform-tools
C:\Users\<你的用户名>\AppData\Local\Android\Sdk\build-tools\36.0.0
```

加入 PATH 后，关闭 VS Code，再重新打开。

### 4. 验证 Android 环境

在 VS Code 终端里执行：

```powershell
adb devices
```

如果能看到设备列表，说明 `adb` 可用。

再执行：

```powershell
aapt2 version
```

如果能看到版本信息，说明 Build-Tools 可用。

### 5. 准备手机或模拟器

可以使用 Android 真机，也可以使用 Android 模拟器。

真机需要：

- 打开开发者选项
- 打开 USB 调试
- 用数据线连接电脑
- 手机上出现授权提示时点击允许

模拟器需要：

- 在 Android Studio 中启动模拟器
- 用 `adb devices` 确认能看到设备

`adb devices` 输出中左侧那一列就是后面网页里要填写的设备号。

例如：

```text
emulator-5554
```

## 二、运行项目

### 1. 用 VS Code 打开项目

在 VS Code 中选择：

```text
File -> Open Folder
```

打开本项目文件夹。

### 2. 打开终端

在 VS Code 中选择：

```text
Terminal -> New Terminal
```

确认终端当前路径是项目目录。

### 3. 创建虚拟环境

第一次运行项目时执行：

```powershell
python -m venv .venv
```

如果项目目录下已经有 `.venv`，这一步可以跳过。

### 4. 激活虚拟环境

```powershell
.\.venv\Scripts\Activate.ps1
```

成功后，终端前面会出现：

```text
(.venv)
```

如果 PowerShell 不允许激活，执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新打开 VS Code 终端，再激活虚拟环境。

### 5. 安装依赖

第一次运行项目时执行：

```powershell
pip install -r requirements.txt
```

如果已经安装过依赖，这一步可以跳过。

如果下载很慢，可以使用清华源：

```powershell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 6. 运行项目自检

```powershell
python scripts\check_project.py
```

看到下面输出说明项目基础检查通过：

```text
project self-check passed
```

### 7. 启动平台

```powershell
python -m uvicorn platform_code.main:app --reload --host 127.0.0.1 --port 8000
```

看到下面输出说明启动成功：

```text
Uvicorn running on http://127.0.0.1:8000
```

然后在浏览器打开：

```text
http://127.0.0.1:8000
```

### 8. 开始测试

网页中填写：

- Device ID：来自 `adb devices`
- APK path：被测试 APK 的本地路径
- 其他参数：先使用默认值

然后点击：

```text
Start test
```

## 常见问题

### python 找不到

说明 Python 没有安装成功，或者没有加入 PATH。重新安装 Python，并勾选：

```text
Add python.exe to PATH
```

### adb 找不到

说明 Android SDK Platform-Tools 没有加入 PATH。把下面目录加入 PATH：

```text
<Android SDK 路径>\platform-tools
```

### aapt2 找不到

说明 Android SDK Build-Tools 没有加入 PATH。把下面目录加入 PATH：

```text
<Android SDK 路径>\build-tools\<版本号>
```

### adb devices 显示 unauthorized

查看手机屏幕，点击允许 USB 调试。

### Activate.ps1 找不到

说明 `.venv` 没有创建成功。先执行：

```powershell
python -m venv .venv
```

### 停止平台

在运行平台的终端中按：

```text
Ctrl + C
```
