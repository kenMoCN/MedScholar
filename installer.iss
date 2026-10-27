; Inno Setup Script for MedScholar
; Generates MedScholar_Setup_Win64.exe

[Setup]
AppId={{5A4F7B3E-91C8-4D2A-8F3E-2A4B8C1D6E5F}
AppName=MedScholar
AppVersion=1.0.0
AppPublisher=MedScholar Team
AppPublisherURL=https://github.com/kenMoCN/MedScholar
AppSupportURL=https://github.com/kenMoCN/MedScholar
AppUpdatesURL=https://github.com/kenMoCN/MedScholar
DefaultDirName={autopf}\MedScholar
DefaultGroupName=MedScholar
AllowNoIcons=yes
OutputDir=.
OutputBaseFilename=MedScholar_Setup_Win64
SetupIconFile=MedScholar.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=MedScholar 安装向导
WelcomeLabel1=欢迎使用 MedScholar 安装向导
WelcomeLabel2=本程序将在您的电脑上安装 MedScholar（医学生智能学习伴侣）。%n%n建议在继续之前关闭其他所有应用程序。
SelectDirLabel3=安装程序将把 MedScholar 安装到以下文件夹中。
SelectDirBrowseLabel=单击“浏览”可选择其他文件夹。
ReadyLabel1=安装程序已准备好开始将 MedScholar 安装到您的电脑上。
FinishedHeadingLabel=MedScholar 安装完成
FinishedLabel=已成功在您的电脑上安装 MedScholar。单击“完成”退出安装向导。

[CustomMessages]
CreateDesktopIcon=创建桌面快捷方式(&D)
AdditionalIcons=附加快捷方式:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "dist\MedScholar\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "MedScholar.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MedScholar"; Filename: "{app}\MedScholar.exe"; IconFilename: "{app}\MedScholar.ico"
Name: "{group}\卸载 MedScholar"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MedScholar"; Filename: "{app}\MedScholar.exe"; IconFilename: "{app}\MedScholar.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\MedScholar.exe"; Description: "启动 MedScholar"; Flags: nowait postinstall skipifsilent
