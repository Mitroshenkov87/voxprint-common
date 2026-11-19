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
- No per-app subfolder.
- Desktop shortcut: one optional Setup checkbox, `Create a desktop shortcut`, off by default, the same in every app (Inno `[Tasks] Name: desktopicon; Flags: unchecked`). It uses the same icon and id as the Start menu launcher.
- An upgrade deletes shortcuts that older builds created (`[InstallDelete]`), because Setup does not remove shortcuts that the new script no longer lists.
- Uninstall removes only this app's shortcut. The folder goes away only when it is empty, that is, when the last suite app is uninstalled. Inno Setup and WiX `RemoveFolder` already remove it only when empty. Never delete it outright (`[UninstallDelete]` on `{group}`, or `Remove-Item` from an uninstall script).

## Icon file

- One multi-size `.ico` per app, frames 16, 24, 32, 48, 64, 128, and 256 px, 32-bit RGBA. PNG-compressed frames are fine on Windows 11.
- Render the frames from the app's master artwork (1024 px PNG). Small sizes may be hand-tuned.

### Icon style

Every app icon belongs to one set:

- A rounded-square tile filling the canvas (corner radius about 18% of the side, 4% transparent margin), dark background `#1b1b23` with a faint top-to-bottom lift to `#2c2c38`.
- One simple glyph for what the app does, centred, about 60% of the tile, in the app's identity colour (`identity` in `ui/tokens.json`). An app without one proposes a colour distinct from the others in its icon review; it is added to `tokens.json`.
- A small flat scene inside an identity-colour frame is allowed instead of a single glyph (Canvas), as long as the identity colour stays dominant and the shapes are flat with no text.
- Flat shapes with at most one soft highlight. No text, letters, photos, people, or fine detail; the glyph must still read at 16 px.
- Final masters are reviewed by Components for consistency with the set before the `.ico` ships.
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

## Moving a per-user install

An app that used to install per user (for example under `%LOCALAPPDATA%`) moves to the per-machine layout. Setup finds the old install from its per-user uninstall key (`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall`), runs its uninstaller silently, removes leftover per-user shortcuts, and keeps user settings and data in the suite home (`voxprint_common.paths.home()`). It fails the install with a clear message if the old uninstaller fails.

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

## Manage models

A Manage models button starts the windowed entry point:

```bash
voxprint-mdl-gui --app canvas
```

`voxprint-mdl-gui` is a GUI script (`pythonw` on Windows), so the window opens without a console. `voxprint-mdl` is a console script. `voxprint-mdl --gui --app canvas` still opens the page, and it keeps the console that launched it. Do not put that command on a button.

The queue process is started with `CREATE_NO_WINDOW` on Windows. A GUI launch also calls `voxprint_common.branding.detach_console()`, which drops a console inherited from `python.exe`. Pass `--manifest <path>` when the app's `mdl.json` is not in the install folder.

## Models that are already on disk

A smoke install, or any install that already has the weight files, should adopt them before `download`. Adopt never deletes or moves the source files. A match is hardlinked into `blobs\<sha256>` (or copied when the source is on another volume), marked verified, and recorded for the app the same way a finished download is. Canvas then finds the model at `views\canvas\<model-id>-<12 hex>`. Anything that does not match is reported and left for `download`, which reuses a blob that adopt already stored.

On a machine that already has the legacy Hugging Face folders next to `blobs`:

```bat
voxprint-mdl adopt --app canvas --manifest "C:\Program Files\VOXPrint AI Suite\Canvas\mdl.json" --model z-image-turbo-bf16-16gb --model qwen3-4b-writer --from "%LOCALAPPDATA%\VOXPrint AI Suite\models\Tongyi-MAI--Z-Image-Turbo" --from "%LOCALAPPDATA%\VOXPrint AI Suite\models\huihui-ai--Huihui-Qwen3-4B-Instruct-2507-abliterated"
```

`qwen3-4b-writer` is optional in that manifest, so it is planned only when `--model` names it. The Z-Image-Turbo files that are already `blobs\<sha256>` are reused. The writer files that are not yet blobs are hardlinked in. Both legacy folders stay. Adopt lists each remaining full copy as `duplicate` and the bytes `cleanup` could reclaim.

When those folders are still separate copies of blobs that already exist, run a dry run, then link them. Delete is the other mode. It removes a file only when a fresh hash of the file and of the blob match.

```bat
voxprint-mdl cleanup --app canvas --manifest "C:\Program Files\VOXPrint AI Suite\Canvas\mdl.json" --legacy "%LOCALAPPDATA%\VOXPrint AI Suite\models\Tongyi-MAI--Z-Image-Turbo" --legacy "%LOCALAPPDATA%\VOXPrint AI Suite\models\huihui-ai--Huihui-Qwen3-4B-Instruct-2507-abliterated"
voxprint-mdl cleanup --apply --mode link --app canvas --manifest "C:\Program Files\VOXPrint AI Suite\Canvas\mdl.json" --legacy "%LOCALAPPDATA%\VOXPrint AI Suite\models\Tongyi-MAI--Z-Image-Turbo" --legacy "%LOCALAPPDATA%\VOXPrint AI Suite\models\huihui-ai--Huihui-Qwen3-4B-Instruct-2507-abliterated"
```

A failed link is skipped and nothing is copied. `--gc` removes blobs nothing references, and only with `--apply`. `shared\manifest.json` entries that still name a legacy file are pointed at the blob before the file is replaced or removed.

```bash
voxprint-mdl adopt --app canvas --manifest "{app}\mdl.json" --from D:\canvas-models
voxprint-mdl download --app canvas --manifest "{app}\mdl.json"
```

`--json` prints the adopted, reused, and skipped files. `--dry-run` only matches path and size. `--gui` shows progress while the files are hashed, one at a time. Repeat `--from` for more than one directory, and `--model` to limit the plan. The full contract is in [mdl.md](mdl.md).

## Release check

Before an RC, on a clean Windows 11 machine:

1. Start shows one `VOXPrint AI Suite` folder with `VOXPrint AI <App>` and the app icon, and nothing else from this app.
2. The installed `voxprint-<app>.exe` shows the app icon in Explorer.
3. The running window and its taskbar button show the app icon, and pinning the taskbar button pins the launcher.
4. Settings > Apps > Installed apps lists `VOXPrint AI <App>` with the icon and publisher.
5. Uninstalling one app leaves the other apps' shortcuts and the folder. Uninstalling the last one removes the folder.
