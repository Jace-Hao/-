; 洗衣管家 · 照片批量上传助手 安装脚本
#define MyAppName "洗衣管家 · 照片批量上传助手"
#define MyAppVersion "1.7"
#define MyAppPublisher "星期衣精致洗衣"
#define MyAppExeName "洗衣管家上传助手.exe"

[Setup]
AppId={{9F3C7E52-1A4D-4B8E-8C61-D2A5F7B39E44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\洗衣管家上传助手
DefaultGroupName=洗衣管家上传助手
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=E:\软件开发\.openclaw\tmp\pkg\installer_out
OutputBaseFilename=洗衣管家上传助手_安装包_v{#MyAppVersion}
SetupIconFile=E:\软件开发\洗衣管家上传助手\assets\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: checkedonce

[Files]
Source: "E:\软件开发\.openclaw\tmp\pkg\dist\洗衣管家上传助手\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\洗衣管家上传助手"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\logo.ico"; Tasks: desktopicon
Name: "{group}\洗衣管家上传助手"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\logo.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动程序"; Flags: nowait postinstall skipifsilent
