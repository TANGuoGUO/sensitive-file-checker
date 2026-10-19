"""Standard-library startup diagnostics. Never enumerate or read user documents."""
import ctypes
import datetime
import faulthandler
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import tempfile
import threading
import traceback

APP_NAME = "SensitiveFileChecker"
BOOTLOADER_NOTE = ("PyInstaller PKG archive errors happen before Python starts. "
                   "This report cannot capture those failures; use the external collector "
                   "and compare the executable size and SHA-256 with the original release.")


def redact(value):
    if isinstance(value, str):
        profile = os.environ.get("USERPROFILE")
        if profile:
            value = re.sub(re.escape(profile), lambda match: "%USERPROFILE%", value, flags=re.IGNORECASE)
            value = re.sub(re.escape(profile.replace("\\", "/")), lambda match: "%USERPROFILE%", value, flags=re.IGNORECASE)
        return value
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def app_version():
    try:
        import version
        return str(getattr(version, "__version__", getattr(version, "VERSION", "1.0.1")))
    except ImportError:
        return "1.0.1"


def create_log_dir(output=None):
    if output:
        choices = [Path(output).expanduser()]
    else:
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + str(os.getpid())
        configured = os.environ.get("SFC_LOG_DIR")
        local = os.environ.get("LOCALAPPDATA")
        base = Path(configured) if configured else Path(local) / APP_NAME / "logs" if local else Path(tempfile.gettempdir()) / APP_NAME / "logs"
        choices = [base / run_id, Path(tempfile.gettempdir()) / APP_NAME / "logs" / run_id]
    last_error = None
    for directory in choices:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=str(directory)):
                pass
            return directory.resolve()
        except OSError as exc:
            last_error = exc
    raise OSError("Cannot create a writable diagnostics directory: " + str(last_error))


class StartupLog:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()

    def stage(self, name, **details):
        event = dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), pid=os.getpid(), stage=name, **details)
        with self.lock, (self.directory / "startup.log").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(redact(event), ensure_ascii=False) + "\n")


class _RedactingStream:
    def __init__(self, stream):
        self.stream = stream

    def write(self, value):
        return self.stream.write(redact(str(value)))

    def flush(self):
        return self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def setup_runtime_log(directory):
    stream = (Path(directory) / "runtime.log").open("a", encoding="utf-8", errors="backslashreplace", buffering=1)
    sys.stdout = _RedactingStream(stream)
    sys.stderr = _RedactingStream(stream)
    try:
        faulthandler.enable(file=stream, all_threads=True)
    except (OSError, RuntimeError):
        pass
    return stream


def write_report(directory, report):
    target = Path(directory) / "report.json"
    target.write_text(json.dumps(redact(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def sanitize_runtime(directory):
    target = Path(directory) / "runtime.log"
    if target.exists():
        text = target.read_text(encoding="utf-8", errors="replace")
        target.write_text(redact(text), encoding="utf-8")


def system32_icu():
    result = {"dll": "System32/icuuc.dll", "available": False, "ucnv_open": False}
    if os.name != "nt":
        result["status"] = "not_windows"
        return result
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = ctypes.create_unicode_buffer(32768)
        kernel32.GetSystemDirectoryW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
        kernel32.GetSystemDirectoryW.restype = ctypes.c_uint
        if not kernel32.GetSystemDirectoryW(buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        library = ctypes.WinDLL(str(Path(buffer.value) / "icuuc.dll"), winmode=0x800)
        result["available"] = True
        result["ucnv_open"] = hasattr(library, "ucnv_open")
    except OSError as exc:
        result["error"] = str(exc)
        result["winerror"] = getattr(exc, "winerror", None)
    return result


def collect_report(include_icu=False):
    os_arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()
    system = {"name": platform.system(), "release": platform.release(), "os_architecture": os_arch,
              "process_bits": struct.calcsize("P") * 8}
    if hasattr(sys, "getwindowsversion"):
        windows = sys.getwindowsversion()
        system["windows"] = {"major": windows.major, "minor": windows.minor, "build": windows.build}
    versions = {"application": app_version(), "python": platform.python_version()}
    for package in ("PySide6", "shiboken6", "pypdf"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "metadata_unavailable"
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    qt_dir = base / "PySide6"
    if not getattr(sys, "frozen", False):
        spec = importlib.util.find_spec("PySide6")
        if spec and spec.submodule_search_locations:
            qt_dir = Path(next(iter(spec.submodule_search_locations)))
    paths = {"QtCore.pyd": qt_dir / "QtCore.pyd", "Qt6Core.dll": qt_dir / "Qt6Core.dll",
             "Qt6Gui.dll": qt_dir / "Qt6Gui.dll", "Qt6Widgets.dll": qt_dir / "Qt6Widgets.dll",
             "qwindows.dll": qt_dir / "plugins" / "platforms" / "qwindows.dll",
             "python39.dll": base / "python39.dll", "vcruntime140.dll": base / "vcruntime140.dll",
             "vcruntime140_1.dll": base / "vcruntime140_1.dll"}
    dependencies = {}
    for name, path in paths.items():
        try:
            dependencies[name] = {"exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else None}
        except OSError as exc:
            dependencies[name] = {"exists": False, "error": str(exc)}
    report = {"format_version": 1, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "system": system, "software": versions, "frozen": bool(getattr(sys, "frozen", False)),
              "qt_environment": {name: os.environ.get(name) for name in
                  ("QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_DEBUG_PLUGINS")},
              "dependencies": dependencies, "bootloader_limit": BOOTLOADER_NOTE,
              "logs": ["startup.log", "runtime.log"]}
    if include_icu:
        report["system32_icu"] = system32_icu()
    return report


def executable_command(arguments):
    if getattr(sys, "frozen", False):
        return [sys.executable] + list(arguments)
    return [sys.executable, str(Path(__file__).with_name("launcher.py"))] + list(arguments)


def run_child(arguments, directory, timeout=None, extra_env=None):
    environment = os.environ.copy()
    if extra_env:
        environment.update(extra_env)
    command = executable_command(list(arguments) + ["--child-log-dir", str(directory)])
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (Path(directory) / "runtime.log").open("ab", buffering=0) as stream:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                       env=environment, creationflags=creationflags)
        except OSError as exc:
            return {"status": "failed", "exit_code": None, "error": str(exc)}
        try:
            code = process.wait(timeout=timeout)
            return {"status": "passed" if code == 0 else "failed", "exit_code": code}
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return {"status": "timeout", "exit_code": process.returncode, "timeout_seconds": timeout}


def show_failure(directory, result):
    message = "程序启动失败。\n诊断目录：{}\n退出码：{}\n请运行随附的启动诊断脚本并提供诊断结果。".format(directory, result.get("exit_code"))
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "敏感资料检查 · 启动失败", 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def run_diagnostics(directory):
    logger = StartupLog(directory)
    logger.stage("diagnostics_started", version=app_version())
    report = collect_report(include_icu=True)
    write_report(directory, report)
    result = run_child(["--probe-qt"], directory, timeout=30, extra_env={"QT_DEBUG_PLUGINS": "1", "QT_QPA_PLATFORM": "windows"} if os.name == "nt" else {"QT_DEBUG_PLUGINS": "1"})
    report["probe"] = result
    logger.stage("diagnostics_finished", **result)
    sanitize_runtime(directory)
    target = write_report(directory, report)
    if sys.stdout:
        print(str(target))
    return 0 if result["status"] == "passed" else 2 if result["status"] == "timeout" else 1


def run_qt_worker(arguments, directory, probe=False):
    runtime = setup_runtime_log(directory)
    logger = StartupLog(directory)
    try:
        logger.stage("qtcore_import_started")
        from PySide6 import QtCore
        logger.stage("qtcore_imported", qt_version=QtCore.qVersion(), pyside_version=QtCore.__version__)
        def qt_message(message_type, context, message):
            runtime.write(redact("Qt [{}]: {}\n".format(message_type, message)))
            runtime.flush()
        QtCore.qInstallMessageHandler(qt_message)
        logger.stage("qtwidgets_import_started")
        from PySide6 import QtWidgets
        logger.stage("qtwidgets_imported")
        import app as gui
        logger.stage("app_imported")
        if probe:
            application = QtWidgets.QApplication([sys.argv[0]])
            logger.stage("application_created")
            application.setStyle(gui.InspectorStyle())
            application.setStyleSheet(gui.STYLE)
            window = gui.MainWindow()
            logger.stage("mainwindow_created")
            application.processEvents()
            QtCore.QTimer.singleShot(200, application.quit)
            code = application.exec()
            window.close()
            logger.stage("probe_completed", exit_code=code)
            return code
        original_init = gui.MainWindow.__init__
        original_show = gui.MainWindow.show
        def window_init(self, *args, **kwargs):
            logger.stage("application_created")
            original_init(self, *args, **kwargs)
            logger.stage("mainwindow_created")
        def window_show(self, *args, **kwargs):
            result = original_show(self, *args, **kwargs)
            logger.stage("mainwindow_shown")
            return result
        gui.MainWindow.__init__ = window_init
        gui.MainWindow.show = window_show
        sys.argv = [sys.argv[0]] + list(arguments)
        code = gui.main()
        logger.stage("gui_finished", exit_code=code)
        return code
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1 if exc.code else 0
        logger.stage("worker_exited", exit_code=code)
        return code
    except BaseException as exc:
        traceback.print_exc()
        logger.stage("worker_failed", error=str(exc), error_type=type(exc).__name__)
        return 1
    finally:
        if "QtCore" in locals():
            QtCore.qInstallMessageHandler(None)
        runtime.flush()
