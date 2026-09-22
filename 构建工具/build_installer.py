# -*- coding: utf-8 -*-
"""生成 .iss 安装脚本并编译（Inno Setup）。"""
import io, os, shutil, subprocess

proj = r"C:\Users\haoyuwei\.openclaw-autoclaw\workspace\洗衣管家上传助手"
pkg = r"C:\Users\haoyuwei\.openclaw-autoclaw\workspace\.openclaw\tmp\pkg"
tmp = r"C:\Users\haoyuwei\.openclaw-autoclaw\workspace\.openclaw\tmp"
inno_lang = r"C:\Program Files (x86)\Inno Setup 6\Languages\ChineseSimplified.isl"
iscc = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

# 1) 安装中文语言包
shutil.copy2(os.path.join(tmp, "ChineseSimplified.isl"), inno_lang)
print("isl installed ->", os.path.exists(inno_lang))

# 2) 写 .iss（UTF-8 BOM）
iss = u"""; 洗衣管家 · 照片批量上传助手 安装脚本
#define MyAppName "洗衣管家 · 照片批量上传助手"
#define MyAppVersion "1.6"
#define MyAppPublisher "星期衣精致洗衣"
#define MyAppExeName "洗衣管家上传助手.exe"

[Setup]
AppId={{9F3C7E52-1A4D-4B8E-8C61-D2A5F7B39E44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\\Programs\\洗衣管家上传助手
DefaultGroupName=洗衣管家上传助手
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=%OUTDIR%
OutputBaseFilename=洗衣管家上传助手_安装包_v{#MyAppVersion}
SetupIconFile=%ICON%
UninstallDisplayIcon={app}\\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: checkedonce

[Files]
Source: "%SRC%\\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\\洗衣管家上传助手"; Filename: "{app}\\{#MyAppExeName}"; IconFilename: "{app}\\assets\\logo.ico"; Tasks: desktopicon
Name: "{group}\\洗衣管家上传助手"; Filename: "{app}\\{#MyAppExeName}"; IconFilename: "{app}\\assets\\logo.ico"

[Run]
Filename: "{app}\\{#MyAppExeName}"; Description: "立即启动程序"; Flags: nowait postinstall skipifsilent
"""
iss = (iss
       .replace("%OUTDIR%", os.path.join(pkg, "installer_out"))
       .replace("%ICON%", os.path.join(proj, "assets", "logo.ico"))
       .replace("%SRC%", os.path.join(pkg, "dist", "洗衣管家上传助手")))
iss_path = os.path.join(tmp, "installer.iss")
io.open(iss_path, "w", encoding="utf-8-sig").write(iss)
print("iss written:", iss_path)

# 3) 编译
os.makedirs(os.path.join(pkg, "installer_out"), exist_ok=True)
r = subprocess.run([iscc, iss_path], capture_output=True)
out = (r.stdout or b"").decode("gbk", errors="replace")
err = (r.stderr or b"").decode("gbk", errors="replace")
io.open(os.path.join(tmp, "iscc_out.txt"), "w", encoding="utf-8").write(out + "\n---STDERR---\n" + err)
print("iscc exit:", r.returncode)
print(out[-1500:])
