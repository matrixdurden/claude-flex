"""Open an installed Desktop binary with private, per-account process state."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import integration, profiles
from .compiler import cli_settings, desktop_profile
from .gateway import Gateway
from .store import FlexError, Store, read_json, write_json


def powershell(script: str) -> str:
    command = shutil.which("powershell.exe")
    if not command:
        raise FlexError("Windows PowerShell is not available.")
    script = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; $OutputEncoding = [Console]::OutputEncoding; " + script
    result = subprocess.run([command, "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    if result.returncode:
        raise FlexError("Claude Desktop could not start.")
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


def windows_host():
    return os.name == "nt" or bool(os.environ.get("WSL_DISTRO_NAME"))


def installed_executable() -> str | None:
    if windows_host():
        result = powershell("$p=Get-AppxPackage -Name Claude | Select-Object -First 1; if ($p) { $exe=Join-Path $p.InstallLocation 'app\\claude.exe'; if (Test-Path -LiteralPath $exe) { Write-Output $exe; exit } }; foreach ($exe in @((Join-Path $env:LOCALAPPDATA 'Programs\\Claude\\Claude.exe'),(Join-Path $env:LOCALAPPDATA 'AnthropicClaude\\claude.exe'))) { if (Test-Path -LiteralPath $exe) { Write-Output $exe; break } }")
        return result or None
    if sys.platform == "darwin":
        for base in (Path("/Applications"), Path.home() / "Applications"):
            binary = base / "Claude.app/Contents/MacOS/Claude"
            if binary.is_file():
                return str(binary)
        return None
    return shutil.which("claude-desktop")


def available() -> bool:
    try:
        return bool(installed_executable())
    except (FlexError, OSError):
        return False


def account_profile(store: Store):
    if isinstance(store, profiles.AccountProfile):
        return store
    label = store.load().get("selected_account")
    if not label:
        raise FlexError("Choose an account first.")
    return profiles.prepare(store, label)


@dataclass
class Layout:
    root: Path
    home: Path
    local: Path
    roaming: Path
    data: Path
    library: Path


def layout(profile: Store) -> Layout:
    if windows_host():
        native = os.environ.get("LOCALAPPDATA") if os.name == "nt" else powershell("[Environment]::GetFolderPath('LocalApplicationData')")
        root = Path(native) if os.name == "nt" else linux_path(native)
        root = root / "Claude-Flex/isolated" / profile.home.name
        local, roaming, home = root / "Local", root / "Roaming", root / "Home"
        data = local / "Claude-3p"
    else:
        root = profile.home / "desktop"
        home, local, roaming = root / "home", root / "local", root / "config"
        data = home / "Library/Application Support/Claude-3p" if sys.platform == "darwin" else roaming / "Claude-3p"
    return Layout(root, home, local, roaming, data, data / "configLibrary")


def native_path(path):
    return windows_path(path) if windows_host() else str(path)


def apply_permissions(store: Store, path: Path | None = None):
    profile = account_profile(store)
    place = layout(profile)
    path = (path or place.home / ".claude/settings.json").resolve()
    if not path.is_relative_to(place.root.resolve()):
        raise FlexError("Desktop settings must stay inside its private profile.")
    previous = read_json(path, {})
    updated = integration.permission_settings(previous, profile.load().get("permission_mode"))
    if previous != updated:
        write_json(path, updated)
    return path


def apply(store: Store, library: Path | None = None, *, activate=False) -> Path:
    profile = account_profile(store)
    place = layout(profile)
    library = (library or place.library).resolve()
    if not library.is_relative_to(place.root.resolve()):
        raise FlexError("Desktop connection must stay inside its private profile.")
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, str(profile.home)))
    path = library / (identifier + ".json")
    write_json(path, desktop_profile(profile))
    write_json(library / "_meta.json", {"appliedId": identifier, "entries": [{"id": identifier, "name": "Claude Flex"}]})
    native_settings = integration.permission_settings(cli_settings(profile), profile.load().get("permission_mode"))
    write_json(place.home / ".claude/settings.json", native_settings)
    write_json(place.home / ".claude/.claude.json", {"hasCompletedOnboarding": True, "theme": "dark"})
    return path


def launch(store: Store):
    binary = installed_executable()
    if not binary:
        raise FlexError("Claude Desktop is not installed.")
    profile = account_profile(store)
    place = layout(profile)
    for path in (place.home, place.local, place.roaming, place.data):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    Gateway(profile).start()
    env = profiles.environment(profile)
    overrides = {
        "HOME": native_path(place.home), "USERPROFILE": native_path(place.home),
        "LOCALAPPDATA": native_path(place.local), "APPDATA": native_path(place.roaming),
        "XDG_CONFIG_HOME": native_path(place.roaming),
        "CLAUDE_CONFIG_DIR": native_path(place.home / ".claude"),
        "CLAUDE_SECURESTORAGE_CONFIG_DIR": native_path(place.home / ".claude"),
    }
    if windows_host():
        # Preserve Windows' actual OS identity/profile for packaged activation.
        # App state and the embedded Claude engine have explicit private roots.
        overrides.pop("USERPROFILE")
        overrides.pop("HOME")
    env.update(overrides)
    args = ["--user-data-dir=" + native_path(place.data)]
    if windows_host():
        marker = place.root / ("launch-" + uuid.uuid4().hex + ".json")
        script = "$ErrorActionPreference='Stop'; try { $p=New-Object System.Diagnostics.ProcessStartInfo; $p.UseShellExecute=$false; "
        script += "$p.FileName=" + ps_quote(binary) + "; $p.Arguments=" + ps_quote(subprocess.list2cmdline(args)) + "; "
        script += "$p.WorkingDirectory=" + ps_quote(native_path(place.root)) + "; "
        for key, value in overrides.items():
            script += "$p.EnvironmentVariables[" + ps_quote(key) + "]=" + ps_quote(value) + "; "
        for key in integration.LEGACY_ENV | {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_USER_DATA_DIR", "CLAUDE_CDP_AUTH"}:
            script += "$p.EnvironmentVariables.Remove(" + ps_quote(key) + "); "
        script += "$child=[System.Diagnostics.Process]::Start($p); @{pid=$child.Id} | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 -LiteralPath " + ps_quote(native_path(marker)) + " } catch { @{error='Desktop could not start'} | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 -LiteralPath " + ps_quote(native_path(marker)) + "; exit 1 }"
        script_path = place.root / "launch.ps1"
        script_path.write_text(script, encoding="utf-8-sig")
        flags = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        helper = subprocess.Popen([shutil.which("powershell.exe"), "-NoProfile", "-File", native_path(script_path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
        for _ in range(100):
            if marker.exists():
                result = read_json(marker)
                marker.unlink()
                if type(result.get("pid")) is int:
                    pid = result["pid"]
                    break
                raise FlexError("Claude Desktop could not start.")
            if helper.poll() not in (None, 0):
                raise FlexError("Claude Desktop could not start.")
            time.sleep(0.1)
        else:
            raise FlexError("Desktop launch timed out.")
    else:
        if sys.platform == "darwin":
            env["CFFIXED_USER_HOME"] = str(place.home)
        process = subprocess.Popen([binary, *args], env=env, cwd=place.root, start_new_session=True)
        pid = process.pid
    write_json(profile.runtime / "desktop-process.json", {"pid": pid, "data": str(place.data)})
    return pid


def open_profile(store: Store, library: Path | None = None) -> bool:
    apply(store, library, activate=True)
    launch(store)
    return False
