"""Official Claude Desktop local profiles, including Windows-from-WSL."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .compiler import desktop_profile
from .store import FlexError, Store, file_lock, read_json, write_json


def powershell(script: str) -> str:
    command = shutil.which("powershell.exe")
    if not command:
        raise FlexError("Windows PowerShell is not available.")
    script = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; $OutputEncoding = [Console]::OutputEncoding; " + script
    result = subprocess.run([command, "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    if result.returncode:
        raise FlexError("Windows desktop integration failed. Check the local PowerShell execution policy.")
    return result.stdout.strip()


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def windows_path(path: Path) -> str:
    if os.name == "nt":
        return str(path)
    result = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def linux_path(value: str) -> Path:
    result = subprocess.run(["wslpath", "-u", value], capture_output=True, text=True, check=True)
    return Path(result.stdout.strip())


def library_path() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "Claude-3p/configLibrary"
    if os.environ.get("WSL_DISTRO_NAME"):
        local = powershell("[Environment]::GetFolderPath('LocalApplicationData')")
        return linux_path(local) / "Claude-3p/configLibrary"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude-3p/configLibrary"
    return Path.home() / ".config/Claude-3p/configLibrary"


def apply_permissions(store: Store, path: Path | None = None):
    if store.load().get("permission_mode") is None:
        return None
    from . import integration

    if path is None:
        home = Path.home()
        if os.environ.get("WSL_DISTRO_NAME"):
            home = linux_path(powershell("[Environment]::GetFolderPath('UserProfile')"))
        path = home / ".claude/settings.json"
    return integration.apply_permissions(store, path)


def apply(store: Store, library: Path | None = None, *, activate=False) -> Path:
    apply_permissions(store)
    library = library or library_path()
    library.mkdir(parents=True, exist_ok=True)
    with file_lock(library / ".claude-flex.lock"):
        meta_path = library / "_meta.json"
        meta = read_json(meta_path, {"appliedId": None, "entries": []})
        entry = next((item for item in meta["entries"] if item.get("name") == "Claude Flex"), None)
        if entry is None:
            entry = {"id": str(uuid.uuid4()), "name": "Claude Flex"}
            meta["entries"].append(entry)
        # Library IDs become filenames. Refuse corrupt metadata before writing.
        try:
            uuid.UUID(entry["id"])
        except (ValueError, TypeError):
            raise FlexError("Invalid Desktop profile ID in configLibrary metadata.") from None
        path = library / (entry["id"] + ".json")
        write_json(path, desktop_profile(store))
        if activate:
            meta["appliedId"] = entry["id"]
        write_json(meta_path, meta)
    return path


def launch(store: Store):
    from .gateway import Gateway

    gateway = Gateway(store)
    gateway.start()
    if os.name == "nt" or os.environ.get("WSL_DISTRO_NAME"):
        app_id = powershell("(Get-StartApps | Where-Object { $_.Name -eq 'Claude' } | Select-Object -First 1).AppID")
        if not app_id:
            raise FlexError("Claude Desktop is not installed.")
        powershell("Start-Process explorer.exe -ArgumentList " + ps_quote("shell:AppsFolder\\" + app_id))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Claude"])
    else:
        command = shutil.which("claude-desktop")
        if not command:
            raise FlexError("Claude Desktop is not installed. Install it, then open the Claude Flex profile.")
        subprocess.Popen([command], start_new_session=True)


def running_instance() -> str | None:
    if os.name == "nt" or os.environ.get("WSL_DISTRO_NAME"):
        value = powershell("Get-Process -Name Claude -ErrorAction SilentlyContinue | Sort-Object StartTime | Select-Object -First 1 | ForEach-Object { '{0}:{1}' -f $_.Id, $_.StartTime.Ticks }")
        return value or None
    command = shutil.which("pgrep")
    if not command:
        return None
    name = "Claude" if sys.platform == "darwin" else "claude-desktop"
    process = subprocess.run([command, "-o", "-x", name], capture_output=True, text=True, check=False)
    return process.stdout.strip() or None


def open_profile(store: Store, library: Path | None = None) -> bool:
    """Apply the selected account's profile, open Desktop, report an actual stale app."""
    library = library or library_path()
    meta = read_json(library / "_meta.json", {"entries": []})
    entry = next((item for item in meta["entries"] if item.get("name") == "Claude Flex"), None)
    previous = {}
    if entry:
        try:
            uuid.UUID(entry["id"])
        except (KeyError, ValueError, TypeError):
            raise FlexError("Invalid Desktop profile ID.") from None
        previous = read_json(library / (entry["id"] + ".json"), {})
    changed = previous != desktop_profile(store) or not entry or meta.get("appliedId") != entry["id"]
    marker = store.runtime / "desktop-restart.json"
    pending = read_json(marker, {})
    instance = running_instance()
    restart_required = bool(instance and (changed or pending.get("instance") == instance))
    apply(store, library, activate=True)
    if restart_required:
        write_json(marker, {"instance": instance})
    else:
        marker.unlink(missing_ok=True)
    launch(store)
    return restart_required


def shortcut(store: Store) -> str:
    if os.name != "nt" and not os.environ.get("WSL_DISTRO_NAME"):
        raise FlexError("Automatic shortcuts currently support Windows and WSL. Use 'desktop launch' on macOS/Linux.")
    local = powershell("[Environment]::GetFolderPath('LocalApplicationData')")
    root = Path(local) / "Claude-Flex" if os.name == "nt" else linux_path(local) / "Claude-Flex"
    root.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("claude-flex")
    if not executable:
        raise FlexError("Install the CLI first: uv tool install .")
    command = "desktop launch"
    if os.environ.get("WSL_DISTRO_NAME"):
        invoke = "& wsl.exe --distribution " + ps_quote(os.environ["WSL_DISTRO_NAME"])
        invoke += " --exec env " + ps_quote("CLAUDE_FLEX_HOME=" + str(store.home))
        invoke += " " + ps_quote(executable) + " " + command
    else:
        invoke = "$env:CLAUDE_FLEX_HOME=" + ps_quote(str(store.home)) + "; & " + ps_quote(executable) + " " + command
    script = root / "Start-Claude-Flex.ps1"
    content = (
        "$ErrorActionPreference='Stop'\ntry {\n" + invoke
        + "\nif ($LASTEXITCODE -ne 0) { throw 'Claude Flex gateway could not start. Run claude-flex doctor.' }\n"
        + "} catch { Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show($_.Exception.Message, 'Claude Flex') | Out-Null; exit 1 }\n"
    )
    script.write_text(content, encoding="utf-8")
    win_script = windows_path(script)
    # COM is needed to create a real Windows .lnk rather than a terminal wrapper.
    ps = """
$desktop=[Environment]::GetFolderPath('Desktop')
$target=Join-Path $desktop SHORTCUT_NAME
$shell=New-Object -ComObject WScript.Shell
$link=$shell.CreateShortcut($target)
$link.TargetPath=Join-Path $env:WINDIR 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'
$link.Arguments='-NoProfile -WindowStyle Hidden -File "' + SCRIPT_PATH + '"'
$link.Description='Claude Code Desktop with your own model and account pool'
$package=Get-AppxPackage -Name Claude | Select-Object -First 1
if ($package) { $icon=Join-Path $package.InstallLocation 'app\\Claude.exe'; if (Test-Path -LiteralPath $icon) { $link.IconLocation=$icon+',0' } }
$link.Save()
Write-Output $target
""".replace("SCRIPT_PATH", ps_quote(win_script)).replace("SHORTCUT_NAME", ps_quote("Claude Flex.lnk"))
    return powershell(ps)
