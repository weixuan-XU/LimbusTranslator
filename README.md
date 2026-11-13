# 边狱公司实时翻译 · LimbusTranslator

把游戏界面上的英文（或中文）实时 OCR 识别后，调用 DeepSeek API 翻译，译文显示在置顶半透明悬浮窗中。只截屏，不注入、不修改游戏进程。

## 运行（源码）

```bash
pip install -r requirements.txt
python limbus_translator.py
```

需要 Windows，并安装对应语言的「光学字符识别」可选功能。API Key 可在程序右键菜单设置，或写入环境变量 `DEEPSEEK_API_KEY`。

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

- `limbus_translator.py` — 主程序
- `glossary.py` — 术语加载与匹配逻辑
- `glossary/terms.txt` — **共享术语表（进 git，可随时增删改）**
- `glossary.local.txt` — 本机私有覆盖（不进 git，可选）
- `LimbusTranslator.exe` — 打包后的可执行文件（可选）
- `使用说明.txt` — 面向最终用户的说明

### 术语表

编辑 `glossary/terms.txt` 后保存即可，程序约每 2 秒自动重载，无需重启。

```
中文=English
English=>中文别名
```
