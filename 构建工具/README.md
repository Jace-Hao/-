# 构建工具说明（维护者用）

这里的脚本用于把 **洗衣管家上传助手** 打包成安装包，以及重新生成图文说明页。它们是开发过程中一直使用的打包脚本（已随 v1.6 构建验证；v1.7 起脚本路径已更新为本机 E 盘配置），**换机器 / 换目录时需先调整脚本顶部的路径变量**。

## 前置依赖

- Python 3.14（含 `PyInstaller` 6.x、`pywin32`、`websocket-client` 等，见 `../requirements.txt`）
- Inno Setup 6（默认安装到 `C:\Program Files (x86)\Inno Setup 6\`）
  - 中文语言包 `ChineseSimplified.isl` 已随本目录提供，`build_installer.py` 会把它复制到 Inno 的 `Languages\` 目录
  - 语言包来源：<https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation>
- 图标文件：`../assets/logo.ico`

## 构建步骤（按顺序执行）

1. **`build_app.py`** — PyInstaller 打包
   - 参数要点：`--windowed --uac-admin`（管理员清单，必须保留！）、`--icon ../assets/logo.ico`、若干 `--hidden-import`
   - 输出到 `pkg/dist/洗衣管家上传助手/`（onedir 形式）
2. **`assemble_payload.py`** — 组装发布载荷
   - 把 `assets/`、`anchors/`、`config.json`、`README.md`、`程序说明.html`、`启动洗衣管家-调试模式.bat` 拷进 dist
3. **`build_installer.py`** — 生成 `installer.iss` 并用 ISCC 编译安装包
   - 输出 `pkg/installer_out/洗衣管家上传助手_安装包_v{版本}.exe`
   - 安装策略：`PrivilegesRequired=lowest`（安装免管理员）、装到 `%LOCALAPPDATA%\Programs\洗衣管家上传助手`、桌面快捷方式（checkedonce）
4. **（可选）`build_manual.py`** — 重新生成 `../程序说明.html`
   - 用 `manual_template.html` 作模板，把 `../文档素材/` 的两张截图 base64 内嵌

## 换机时需要调整的路径

- 每个脚本顶部的 `proj` / `pkg` / `tmp` 变量（当前为本机 E 盘配置：项目目录 + `.openclaw\tmp` 中间产物目录）
- `build_installer.py` 里的 `iscc`、`inno_lang` 路径（Inno 安装位置）

## 版本与发布约定

- 版本号位置：`../main.py` 顶部 `VERSION = "x.y"`
- 打包前同步更新：`../CHANGELOG.md`（新版本段落）、`../README.md`（安装包文件名）、`../程序说明.html` 版本号
- 安装包命名：`洗衣管家上传助手_安装包_v{版本}.exe`
- GitHub 同步：提交源码 + 打 Release（附件使用 **ASCII 文件名**，如 `Xiyiguanjia-Uploader-Setup-v1.6.exe`；中文显示名放附件 `label`——GitHub 附件名不支持中文，会被打碎）
- 升级安装前先关闭正在运行的工具（否则 exe 被占用）
