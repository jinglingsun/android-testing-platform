from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import uiautomator2 as u2

from .actions import Action
from .state import state_hash


ALLOW_BUTTON_PATTERN = r"(?i)(allow|\u5141\u8bb8|\u59cb\u7ec8\u5141\u8bb8|\u4ec5\u5728\u4f7f\u7528\u4e2d\u5141\u8bb8)"


class DeviceAdapter:
    def __init__(self, device_id: str, min_interval: float = 1.0) -> None:
        self.device_id = device_id
        self.min_interval = min_interval
        self._ensure_command("adb", "Android Platform Tools is not in PATH. Please make sure adb.exe can run in this terminal.")
        self.d = u2.connect(device_id)
        self.package_name: str | None = None
        self._last_action_at = 0.0

    def install_apk(self, apk_path: str) -> str:
        if not apk_path.strip():
            raise RuntimeError("APK path is required.")
        apk = Path(apk_path).expanduser().resolve()
        if not apk.exists() or not apk.is_file():
            raise RuntimeError(f"APK file does not exist: {apk}")
        if apk.suffix.lower() != ".apk":
            raise RuntimeError(f"APK path must point to an .apk file: {apk}")

        self._run_adb("install", "-r", str(apk), check=True)
        package = self._apk_package(str(apk))
        if not package:
            raise RuntimeError(
                "Cannot parse APK package name. Please install Android SDK Build Tools "
                "and make sure at least one of aapt, aapt2, or apkanalyzer is in PATH."
            )
        self.package_name = package
        return package

    def reset_app(self) -> list[Action]:
        actions = self.reset_app_actions()
        for action in actions:
            self.perform(action)
        return actions

    def reset_app_actions(self) -> list[Action]:
        if not self.package_name:
            return []
        return [
            Action("force_stop", selector={"packageName": self.package_name}, system=True),
            Action("clear_app", selector={"packageName": self.package_name}, system=True),
            Action("clear_logcat", system=True),
            Action("restart", selector={"packageName": self.package_name}, system=True),
        ]

    def auto_allow_permissions(self) -> list[Action]:
        actions: list[Action] = []
        for _ in range(5):
            action = self.permission_allow_action()
            if action is None:
                break
            self.perform(action)
            actions.append(action)
            time.sleep(0.5)
        return actions

    def permission_allow_action(self) -> Action | None:
        button = self.d(textMatches=ALLOW_BUTTON_PATTERN)
        if not button.exists:
            return None
        return Action("permission_allow", selector={"textMatches": ALLOW_BUTTON_PATTERN}, system=True)

    def dump_xml(self) -> str:
        xml = self.d.dump_hierarchy(compressed=False)
        if not xml:
            raise RuntimeError("uiautomator2 returned empty UI hierarchy. Please unlock the device and keep the target app visible.")
        return str(xml)

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.d.screenshot(str(path))

    def current_state_hash(self) -> str:
        return state_hash(self.dump_xml())

    def current_package(self) -> str | None:
        return self.d.app_current().get("package")

    def wait_stable(self, poll_ms: int, samples: int, timeout_sec: float) -> str:
        deadline = time.time() + timeout_sec
        recent: list[str] = []
        last_xml = self.dump_xml()
        while time.time() < deadline:
            last_xml = self.dump_xml()
            recent.append(state_hash(last_xml))
            recent = recent[-samples:]
            if len(recent) == samples and len(set(recent)) == 1:
                return last_xml
            time.sleep(poll_ms / 1000)
        return last_xml

    def perform(self, action: Action) -> None:
        elapsed = time.time() - self._last_action_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        if action.kind == "click" and action.coordinates:
            self.d.click(*action.coordinates)
        elif action.kind == "long_click" and action.coordinates:
            self.d.long_click(*action.coordinates)
        elif action.kind == "input" and action.coordinates:
            self.d.click(*action.coordinates)
            self.d.send_keys(action.text or "codex-test", clear=True)
        elif action.kind == "swipe":
            self.d.swipe(500, 1500, 500, 400)
        elif action.kind == "back":
            self.d.press("back")
        elif action.kind == "restart":
            if self.package_name:
                self.d.app_start(self.package_name)
        elif action.kind == "force_stop":
            if self.package_name:
                self._run_adb("shell", "am", "force-stop", self.package_name)
        elif action.kind == "clear_app":
            if self.package_name:
                self._run_adb("shell", "pm", "clear", self.package_name)
        elif action.kind == "clear_logcat":
            self._run_adb("logcat", "-c")
        elif action.kind == "permission_allow":
            button = self.d(textMatches=ALLOW_BUTTON_PATTERN)
            if button.exists:
                button.click()
        self._last_action_at = time.time()

    def target_fatal_exception(self) -> str | None:
        if not self.package_name:
            return None
        output = self._run_adb("logcat", "-d", "-t", "1500", "-v", "brief")
        if "FATAL EXCEPTION" not in output or self.package_name not in output:
            return None
        lines = output.splitlines()
        start = next((i for i, line in enumerate(lines) if "FATAL EXCEPTION" in line), -1)
        if start < 0:
            return None
        block = "\n".join(lines[start : start + 60])
        return block if self.package_name in block else None

    def ensure_in_target_app(self) -> list[Action]:
        if not self.package_name:
            return []
        current = self.current_package()
        if current == self.package_name:
            return []
        self.d.press("back")
        time.sleep(1)
        if self.d.app_current().get("package") == self.package_name:
            return [Action("back", system=True)]
        self.d.app_start(self.package_name)
        return [Action("restart", system=True)]

    def _run_adb(self, *args: str, check: bool = False) -> str:
        adb = self._ensure_command("adb", "adb.exe was not found. Add Android SDK platform-tools to PATH.")
        proc = subprocess.run(
            [adb, "-s", self.device_id, *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = _completed_output(proc)
        if check and proc.returncode != 0:
            raise RuntimeError(output.strip() or f"adb command failed: {' '.join(args)}")
        return output

    def _apk_package(self, apk_path: str) -> str | None:
        aapt = shutil.which("aapt")
        if aapt:
            proc = _run_text_command([aapt, "dump", "badging", apk_path])
            for line in _completed_stdout(proc).splitlines():
                if line.startswith("package:"):
                    for part in line.split():
                        if part.startswith("name="):
                            return part.split("=", 1)[1].strip("'")

        aapt2 = shutil.which("aapt2")
        if aapt2:
            proc = _run_text_command([aapt2, "dump", "packagename", apk_path])
            package = _completed_stdout(proc).strip().splitlines()
            if package:
                return package[0].strip()

        apkanalyzer = shutil.which("apkanalyzer")
        if apkanalyzer:
            proc = _run_text_command([apkanalyzer, "manifest", "application-id", apk_path])
            package = _completed_stdout(proc).strip().splitlines()
            if package:
                return package[0].strip()

        return None

    @staticmethod
    def _ensure_command(name: str, message: str) -> str:
        path = shutil.which(name)
        if not path:
            raise RuntimeError(message)
        return path


def _completed_stdout(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout if isinstance(proc.stdout, str) else ""


def _completed_output(proc: subprocess.CompletedProcess) -> str:
    stdout = proc.stdout if isinstance(proc.stdout, str) else ""
    stderr = proc.stderr if isinstance(proc.stderr, str) else ""
    return stdout + stderr


def _run_text_command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
