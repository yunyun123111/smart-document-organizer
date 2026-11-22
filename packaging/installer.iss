; 智能文档整理系统 - Inno Setup 安装脚本
; 编译：ISCC.exe packaging\installer.iss
; 前置：先执行 pyinstaller packaging/smartdoc.spec 生成 dist\SmartDocOrganizer\

#define MyAppName "智能文档整理系统"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SmartDoc"
#define MyAppExeName "SmartDocOrganizer.exe"

[Setup]
AppId={{8F2A6C1E-9B3D-4A7E-8C21-5D6E7F809A12}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SmartDocOrganizer
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 普通用户安装到当前用户目录，不触发 UAC
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\release
OutputBaseFilename=SmartDocOrganizer-Setup-{#MyAppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
; 升级时安装器会关闭占用文件的程序，装完自动拉起新版本
RestartApplications=yes
; 数据在 %APPDATA%，卸载不删除

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\SmartDocOrganizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 不加 skipifsilent：设置页「立即更新」用静默安装，装完要自动打开新版本
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall

[Code]
function WebView2Installed(): Boolean;
var
  Value: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value);
end;

procedure InitializeWizard();
begin
  if not WebView2Installed() then
    MsgBox('未检测到 Microsoft Edge WebView2 运行时。' + #13#10 +
           '系统会先尝试用内置浏览器打开；若窗口空白，请安装 WebView2 Runtime 后重试。',
           mbInformation, MB_OK);
end;
