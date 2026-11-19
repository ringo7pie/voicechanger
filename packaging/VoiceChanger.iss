; VoiceChanger のインストーラー（Inno Setup 6）。tools\build_installer.ps1 から ISCC で作る。
; 渡す定義: /DAppVersion=<版>  /DDistDir=<PyInstaller の出力>  /DVbCableDir=<VB-CABLE の展開先>  /DOutputDir=<出力先>
;
; - 既定は全ユーザー向け（Program Files、管理者）。「自分だけ」も選べる（VB-CABLE は入れられない）。
; - VB-CABLE（Driver Pack46）は AS IS で同梱する。規約（https://vb-audio.com/Services/licensing.htm）の条件として、
;   www.vb-cable.com と「VB-CABLE is a donationware, all participations are welcome」を画面と THIRD-PARTY-NOTICES に出す。
;   無人インストールの引数は付属の readme に無いので、セットアップ画面を出して利用者に「Install Driver」を押してもらう。
; - データ（%LOCALAPPDATA%\VoiceChanger）と VB-CABLE は、アンインストールで消さない。

#ifndef AppVersion
  #error AppVersion を /DAppVersion= で渡してください
#endif
#ifndef DistDir
  #define DistDir "..\out\pyinstaller\dist\VoiceChanger"
#endif
#ifndef VbCableDir
  #define VbCableDir "..\out\installer-staging\vbcable"
#endif
#ifndef OutputDir
  #define OutputDir "..\out\installer"
#endif

[Setup]
AppId={{6E0C7A52-3B8F-4D2A-9C41-7A5E2B9D1F30}
AppName=VoiceChanger
AppVersion={#AppVersion}
AppVerName=VoiceChanger {#AppVersion}
AppPublisher=ringo7pie
AppPublisherURL=https://github.com/ringo7pie/voicechanger
AppSupportURL=https://github.com/ringo7pie/voicechanger/issues
AppUpdatesURL=https://github.com/ringo7pie/voicechanger/releases
DefaultDirName={autopf}\VoiceChanger
DefaultGroupName=VoiceChanger
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=VoiceChanger-Setup-{#AppVersion}
SetupIconFile=..\voicechanger\gui\voicechanger.ico
UninstallDisplayIcon={app}\VoiceChanger.exe
UninstallDisplayName=VoiceChanger {#AppVersion}
LicenseFile=..\LICENSE
InfoBeforeFile=info-before.txt
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作る"; GroupDescription: "ショートカット:"
Name: "vbcable"; Description: "VB-CABLE（仮想マイク CABLE Input / CABLE Output）を入れる ― 寄付ウェア、www.vb-cable.com"; GroupDescription: "仮想マイク（録画・配信に使うとき）:"; Check: CanInstallVbCable

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#VbCableDir}\*"; DestDir: "{app}\vbcable"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\VoiceChanger"; Filename: "{app}\VoiceChanger.exe"
Name: "{autodesktop}\VoiceChanger"; Filename: "{app}\VoiceChanger.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\vbcable\VBCABLE_Setup_x64.exe"; WorkingDir: "{app}\vbcable"; Tasks: vbcable; Flags: waituntilterminated; StatusMsg: "VB-CABLE のセットアップを開いています。画面の「Install Driver」を押し、終わるまで待ってください…"
Filename: "{app}\VoiceChanger.exe"; Description: "VoiceChanger を起動する"; Flags: nowait postinstall skipifsilent unchecked
Filename: "https://github.com/ringo7pie/voicechanger#readme"; Description: "使い方（追加の部品・OBS の設定）を開く"; Flags: shellexec nowait postinstall skipifsilent

[Code]
function VbCableInstalled: Boolean;
var
  FindRec: TFindRec;
begin
  { 導入済みなら DriverStore に vbmmecable64_win10.inf_amd64_* がある（2026-11-19 にこの名前を確認） }
  Result := FindFirst(ExpandConstant('{sys}\DriverStore\FileRepository\vbmmecable64_*'), FindRec);
  if Result then
    FindClose(FindRec);
end;

function CanInstallVbCable: Boolean;
begin
  Result := IsAdminInstallMode and not VbCableInstalled;
end;

procedure InitializeWizard;
begin
  if VbCableInstalled then
    Log('VB-CABLE: 導入済み（DriverStore に vbmmecable64_* がある）')
  else
    Log('VB-CABLE: 未導入');
end;

function NeedRestart: Boolean;
begin
  Result := WizardIsTaskSelected('vbcable');
end;
