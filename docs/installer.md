# Windows installer and branding standard

Every suite app installs the same way on Windows. The names come from `voxprint_common.branding.install_identity("<App>")`; the table shows Canvas.

| Item | Rule | Canvas |
|---|---|---|
| Display name | `VOXPrint AI <App>` | `VOXPrint AI Canvas` |
| Install folder | `%ProgramFiles%\VOXPrint AI Suite\<App>` | `C:\Program Files\VOXPrint AI Suite\Canvas` |
| GUI / CLI executable | lower-case kebab | `voxprint-canvas.exe`, `voxprint-canvas-cli.exe` |
| Icon | `{app}\assets\voxprint-<app>.ico` for new icons; an existing installed `.ico` path may stay | `assets\voxprint-canvas.ico` |
| Start menu | `VOXPrint AI Suite\VOXPrint AI <App>.lnk` | `VOXPrint AI Suite\VOXPrint AI Canvas.lnk` |
| AppUserModelID | `VOXPrint.AISuite.<App without spaces>` | `VOXPrint.AISuite.Canvas` |
| Publisher | `Aleksandr Mitroshenkov` | |

## Start menu

- One folder, `VOXPrint AI Suite`, shared by every app. Per-machine installs put it in the all-users Programs folder (`{group}` with `DefaultGroupName=VOXPrint AI Suite`, or `{autoprograms}\VOXPrint AI Suite`).
- The folder holds only app launchers, one per app, named `VOXPrint AI <App>`. No Uninstall shortcut, no guide, readme, diagnostics, or website shortcuts. Guides open from the app's Help menu. Uninstall goes through Settings > Apps > Installed apps.
- No per-app subfolder. Desktop shortcuts are optional and use the same icon and id.
- An upgrade deletes shortcuts that older builds created (`[InstallDelete]`), because Setup does not remove shortcuts that the new script no longer lists.
- Uninstall removes only this app's shortcut. The folder goes away only when it is empty, that is, when the last suite app is uninstalled. Inno Setup and WiX `RemoveFolder` already remove it only when empty. Never delete it outright (`[UninstallDelete]` on `{group}`, or `Remove-Item` from an uninstall script).

## Icon file

- One multi-size `.ico` per app, frames 16, 24, 32, 48, 64, 128, and 256 px, 32-bit RGBA. PNG-compressed frames are fine on Windows 11.
- Render the frames from the app's master artwork (1024 px PNG). Small sizes may be hand-tuned.
- `python -m voxprint_common.branding pack out.ico 16.png 24.png ... 256.png` packs pre-rendered frames. `python -m voxprint_common.branding check out.ico` fails when a size is missing; run it in the app's release check. Apps that scale one PNG with Pillow (Enlarge `tools/make_icon.py`) may keep that and run `check`.
- The same `.ico` is used for the executable, the shortcut, the uninstall entry, the setup program (`SetupIconFile`), and the window.

## Executable icon

A `venv\Scripts\*.exe` made by pip or uv is a generic trampoline with no app icon. It is not a shortcut target. Each app ships its own launcher compiled with the icon, then points the shortcut and the uninstall entry at it.

The .NET Framework C# compiler ships with Windows, so Inno Setup can compile the launcher while the script is preprocessed, the same way Canvas already builds `vxsigcheck.exe`. Frozen builds (PyInstaller) pass the same `.ico` to `--icon` instead.

```pascal
#define Launcher AddBackslash(SourcePath) + "voxprint-canvas.exe"
#define LauncherExit Exec(Csc, '/nologo /optimize+ /target:winexe /win32icon:"' + AddBackslash(SourcePath) + '..\..\assets\voxprint-canvas.ico" /out:"' + Launcher + '" "' + AddBackslash(SourcePath) + 'launcher.cs"', SourcePath, 1, 0)
#if LauncherExit != 0
  #error launcher.cs did not compile
#endif

[Files]
Source: "voxprint-canvas.exe"; DestDir: "{app}"; Flags: ignoreversion
```

`launcher.cs` (set `Module` and `Title` per app):

```csharp
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

internal static class Program
{
    private const string Module = "canvas.app.main";
    private const string Title = "VOXPrint AI Canvas";

    private static int Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, "venv", "Scripts", "pythonw.exe");
        if (!File.Exists(py))
        {
            MessageBox(IntPtr.Zero, Title + " is not set up yet:\r\n" + py, Title, 0x10);
            return 1;
        }
        var start = new ProcessStartInfo(py, "-I -m " + Module + Tail(Environment.CommandLine));
        start.WorkingDirectory = dir;
        start.UseShellExecute = false;
        using (Process process = Process.Start(start))
        {
            process.WaitForExit();
            return process.ExitCode;
        }
    }

    // Everything after argv[0], passed through unchanged.
    private static string Tail(string line)
    {
        int i = 0;
        if (line.StartsWith("\""))
        {
            i = line.IndexOf('"', 1);
            i = i < 0 ? line.Length : i + 1;
        }
        else
        {
            while (i < line.Length && line[i] != ' ' && line[i] != '\t') i++;
        }
        return i < line.Length ? line.Substring(i) : "";
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBox(IntPtr owner, string text, string caption, uint type);
}
```

A console twin for `-cli.exe` uses `/target:exe` and `python.exe`. Movie Dubber `installer/launchers/launcher.cs` and Clarity `installer/launchers/Launcher.cs` are working examples.

## Taskbar and window

The process that owns the window is `pythonw.exe`. Without an explicit id, the taskbar groups the window under Python and shows its icon. Before the first window opens:

```python
from voxprint_common import branding

ident = branding.install_identity("Canvas")
branding.set_app_user_model_id(ident.app_user_model_id)
app.setWindowIcon(QIcon(str(app_dir / "assets" / ident.icon_file)))
```

The shortcut carries the same id (`AppUserModelID:` in Inno Setup `[Icons]`, `System.AppUserModel.ID` in WiX).

## Apps & features

| Value | Inno Setup | WiX |
|---|---|---|
| DisplayName `VOXPrint AI <App>` (no version) | `AppName`, `UninstallDisplayName={#MyAppName}` | `Package/@Name` |
| DisplayIcon | `UninstallDisplayIcon={app}\assets\voxprint-<app>.ico` | `ARPPRODUCTICON` property and `Icon` element |
| Publisher | `AppPublisher=Aleksandr Mitroshenkov` | `Package/@Manufacturer` |
| DisplayVersion | `AppVersion` | `Package/@Version` |

## Inno Setup snippet

```ini
#define MyAppName "VOXPrint AI Canvas"
#define MySuiteName "VOXPrint AI Suite"
#define MyAppId "VOXPrint.AISuite.Canvas"
#define MyIcon "voxprint-canvas.ico"

[Setup]
AppName={#MyAppName}
AppPublisher=Aleksandr Mitroshenkov
DefaultDirName={autopf}\{#MySuiteName}\Canvas
DefaultGroupName={#MySuiteName}
DisableProgramGroupPage=yes
SetupIconFile=..\..\assets\{#MyIcon}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\assets\{#MyIcon}

[Files]
Source: "..\..\assets\{#MyIcon}"; DestDir: "{app}\assets"; Flags: ignoreversion

[InstallDelete]
; Shortcuts from older builds.
Type: files; Name: "{group}\Uninstall {#MyAppName}.lnk"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\voxprint-canvas.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\{#MyIcon}"; AppUserModelID: "{#MyAppId}"
```

No other `[Icons]` lines and no `[UninstallDelete]` entry for `{group}`.

## Release check

Before an RC, on a clean Windows 11 machine:

1. Start shows one `VOXPrint AI Suite` folder with `VOXPrint AI <App>` and the app icon, and nothing else from this app.
2. The installed `voxprint-<app>.exe` shows the app icon in Explorer.
3. The running window and its taskbar button show the app icon, and pinning the taskbar button pins the launcher.
4. Settings > Apps > Installed apps lists `VOXPrint AI <App>` with the icon and publisher.
5. Uninstalling one app leaves the other apps' shortcuts and the folder. Uninstalling the last one removes the folder.
