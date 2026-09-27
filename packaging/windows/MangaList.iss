; Inno Setup script for Manga List (Windows x64, per-user, no administrator rights).
;
;   iscc /DAppVersion=1.2.3 /DAppVersionNumeric=1.2.3 packaging\windows\MangaList.iss
;
; Input: the PyInstaller onedir build in dist\MangaList (and build\icons\MangaList.ico if present).
; Output: package\MangaList-v<AppVersion>-windows-x64-setup.exe
;
; - Installs to %LOCALAPPDATA%\Programs\MangaList (PrivilegesRequired=lowest), Start-menu entry,
;   optional desktop shortcut, uninstall entry under the current user.
; - Upgrade = run a newer setup: same AppId, installs over the old version (the old _internal folder
;   is removed first so no stale libraries remain), running copies are closed first.
; - User data lives in %LOCALAPPDATA%\MangaList (settings, MangaUpdates cache, logs) - outside {app},
;   so neither an upgrade nor an uninstall touches it.
; - Never change AppId: it is how Windows recognises an installed Manga List.
; - Not signed. Signing plugs in with a SignTool directive (see docs/packaging.md).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.0.0"
#endif
#define RootDir AddBackslash(SourcePath) + "..\.."
#define IconFile RootDir + "\build\icons\MangaList.ico"

[Setup]
AppId={{A386EF83-639E-48AE-A584-1863DF572F7B}
AppName=Manga List
AppVersion={#AppVersion}
AppVerName=Manga List {#AppVersion}
AppPublisher=dixit92
AppPublisherURL=https://github.com/dixit92/manga-list
AppSupportURL=https://github.com/dixit92/manga-list/issues
AppUpdatesURL=https://github.com/dixit92/manga-list/releases
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductVersion={#AppVersionNumeric}
VersionInfoDescription=Manga List setup
DefaultDirName={autopf}\MangaList
DefaultGroupName=Manga List
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#RootDir}\package
OutputBaseFilename=MangaList-v{#AppVersion}-windows-x64-setup
#if FileExists(IconFile)
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\MangaList.exe
UninstallDisplayName=Manga List
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade replaces the bundled libraries wholesale.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#RootDir}\dist\MangaList\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RootDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RootDir}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
; AppUserModelID matches the one the app sets, so the taskbar groups its windows under this shortcut.
Name: "{autoprograms}\Manga List"; Filename: "{app}\MangaList.exe"; AppUserModelID: "manga_list.classifier.1"
Name: "{autodesktop}\Manga List"; Filename: "{app}\MangaList.exe"; AppUserModelID: "manga_list.classifier.1"; Tasks: desktopicon

[Run]
Filename: "{app}\MangaList.exe"; Description: "{cm:LaunchProgram,Manga List}"; Flags: nowait postinstall skipifsilent
