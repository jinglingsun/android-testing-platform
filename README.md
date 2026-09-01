# Android Teaching Test Platform

本项目是一个本地教学平台原型，用 Python + FastAPI + Jinja2 + SQLite + openatx/uiautomator2 实现 Android 应用自动探索测试。

完整运行教程见 [source_code/RUNNING_GUIDE.md](source_code/RUNNING_GUIDE.md)。

## 运行

前置条件：

- Windows 已安装 Python 3.10+。
- 已安装 Android Platform Tools，`adb` 在 PATH 中。
- 推荐安装 Android Build Tools，并让 `aapt` 在 PATH 中，用于解析 APK 包名。
- 手机已打开 USB 调试；模拟器或真机可通过 `adb devices` 看到。

```powershell
cd source_code
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn platform_code.main:app --reload --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`。

如果你的 Windows 只支持 `py` 启动器：

```powershell
cd source_code
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn platform_code.main:app --reload --host 127.0.0.1 --port 8000
```

运行前检查设备：

```powershell
adb devices
```

把输出中的设备号填到网页的“设备号”输入框。APK 路径填写本机绝对路径，例如 `C:\apps\demo.apk`。

## 第一版能力

- 本地网页配置测试任务。
- 用户输入 `adb devices` 得到的设备号，填写 APK 路径。
- 平台安装 APK、解析目标包名、清理应用数据、启动应用。
- 支持随机、DFS、BFS 三类探索算法。
- 支持用户在 `source_code/plugins/algorithms` 中新增探索算法。
- 支持用户在 `source_code/plugins/properties` 中新增性质函数。
- 每个 UI 动作后截图、保存 UI XML、记录可重放事件。
- 通过目标应用 `FATAL EXCEPTION` 捕获崩溃。
- 通过性质 final assert 捕获非崩溃错误。
- 每个错误生成独立 HTML，总报告展示任务概况。
- 错误报告支持标记误报，并保存误报规则。
- 支持重放入口，按记录的事件序列复现。

## 代码结构

- `source_code/platform_code/main.py`: FastAPI 入口和页面路由。
- `source_code/platform_code/database.py`: SQLite 初始化与简单访问层。
- `source_code/platform_code/runner.py`: 测试任务执行主循环。
- `source_code/platform_code/device.py`: adb/uiautomator2 设备适配层。
- `source_code/platform_code/state.py`: UI 树规范化与状态哈希。
- `source_code/platform_code/actions.py`: UI 动作模型和候选动作生成。
- `source_code/platform_code/algorithms.py`: 算法插件加载与内置算法。
- `source_code/platform_code/properties.py`: 性质插件加载与执行协议。
- `source_code/platform_code/reports.py`: HTML 报告生成。
- `source_code/plugins/algorithms`: 学生新增算法的位置。
- `source_code/plugins/properties`: 学生新增性质的位置。

## UI 状态规范化规则

状态哈希只用于“页面是否相似”的判断，不影响截图、控件数量断言和重放。

- 保留：`class`、`resource-id`、`content-desc`、`text`、常见交互属性、父子结构。
- 忽略：`bounds`、`index`、`package`、`focused`、属性顺序。
- 仅纳入可见节点。
- 文本会去除首尾空白并合并连续空白。
- 明显动态值会替换为占位符：时间、纯数字、UUID、URL。
- 同一个列表中重复出现的同类型结构，在状态哈希中只算一次。
- 原始 XML 和 bounds 会完整保存，用于报告、高亮、重放和 count 断言。

## 性质函数协议

性质文件放在 `source_code/plugins/properties/*.py`，函数名以 `test_` 开头。

约定：

- 第一个 UI 动作前的 `require_*` 是前置条件，失败表示当前页面不适用。
- 中间的 `require_*` 或动作失败表示性质执行不完整，不算错误。
- 最后必须有且只有一个 `final_assert_*`。
- 只有最后的 `final_assert_*` 失败才算性质违反。

示例见 `source_code/plugins/properties/sample_input_box.py`。
