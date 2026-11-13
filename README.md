# 边狱公司实时翻译 · LimbusTranslator

把游戏界面上的英文（或中文）实时 OCR 识别后，调用 DeepSeek API 翻译，译文显示在置顶半透明悬浮窗中。只截屏，不注入、不修改游戏进程。

**推荐用源码运行（`python limbus_translator.py`）**：改术语表、调界面、跟仓库同步都更方便。  
也可下载 [Releases](https://github.com/weixuan-XU/LimbusTranslator/releases) 里的 `LimbusTranslator.exe` 一键使用；请同时下载 `glossary.zip`，解压后与 exe 放在同一目录。

需要 Windows，并安装对应语言的「光学字符识别」可选功能。API Key 可在程序右键菜单设置，或写入环境变量 `DEEPSEEK_API_KEY`。

## 运行（源码 · 推荐）

```bash
pip install -r requirements.txt
python limbus_translator.py
```

## 下载 exe

- 最新包：[Releases · v1.0.0](https://github.com/weixuan-XU/LimbusTranslator/releases/tag/v1.0.0)
- 资源：`LimbusTranslator.exe` + `glossary.zip`

## 自行打包 exe

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

生成 `LimbusTranslator.exe`（无控制台窗口）。请保留旁边的 `glossary/` 目录以便编辑术语表。

## 热键

| 按键 | 作用 |
|------|------|
| F2 | 框选区域 → 实时翻译 |
| F3 | 整屏翻译（单次） |
| F4 | 停止 / 继续 |
| F6 | 切换鼠标穿透 |
| F7 | 隐藏 / 显示悬浮窗 |
| Ctrl+S | 复制译文 |
| Ctrl+E | 清空输出 |
| Ctrl+Q | 退出程序 |

## 文件

- `limbus_translator.py` — 主程序（推荐直接跑这个）
- `glossary.py` — 术语加载与匹配逻辑
- `glossary/terms.txt` — **共享术语表（进 git，可随时增删改）**
- `glossary.local.txt` — 本机私有覆盖（不进 git，可选）
- `LimbusTranslator.exe` — 打包产物（见 Releases，不进 git）
- `使用说明.txt` — 面向最终用户的说明

### 术语表

编辑 `glossary/terms.txt` 后保存即可，程序约每 2 秒自动重载，无需重启。

```
中文=English
English=>中文别名
```
