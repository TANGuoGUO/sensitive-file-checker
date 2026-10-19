"""Pure-standard-library bootstrap; PySide6 is imported only in child workers."""
import multiprocessing
import sys


def main(argv=None):
    # Also consumes PyInstaller multiprocessing worker switches before any GUI.
    multiprocessing.freeze_support()
    import argparse
    import traceback
    import diagnostics
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--gui-worker", action="store_true")
    parser.add_argument("--probe-qt", action="store_true")
    parser.add_argument("--child-log-dir")
    args, forwarded = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    try:
        directory = diagnostics.create_log_dir(args.child_log_dir or (args.output if args.diagnostics else None))
    except OSError as exc:
        if sys.stderr:
            print(diagnostics.redact(str(exc)), file=sys.stderr)
        return 3
    logger = diagnostics.StartupLog(directory)
    logger.stage("bootstrap_ready", version=diagnostics.app_version())
    if args.gui_worker or args.probe_qt:
        return diagnostics.run_qt_worker(forwarded, directory, probe=args.probe_qt)
    try:
        if args.diagnostics:
            return diagnostics.run_diagnostics(directory)
        report = diagnostics.collect_report()
        diagnostics.write_report(directory, report)
        logger.stage("gui_child_starting")
        result = diagnostics.run_child(["--gui-worker"] + forwarded, directory)
        logger.stage("gui_child_exited", **result)
        report["launch"] = result
        diagnostics.sanitize_runtime(directory)
        diagnostics.write_report(directory, report)
        if result["status"] != "passed":
            diagnostics.show_failure(directory, result)
        return 0 if result["status"] == "passed" else 1
    except Exception as exc:
        logger.stage("bootstrap_failed", error=str(exc))
        with (directory / "runtime.log").open("a", encoding="utf-8") as stream:
            stream.write(diagnostics.redact(traceback.format_exc()))
        if not args.diagnostics:
            diagnostics.show_failure(directory, {"exit_code": 1})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
