"""命令列入口：quickseemd [open|run|tools|convert|render]。

- quickseemd                       打開主程式
- quickseemd open [--tool ID] 檔案…  打開主程式並帶入檔案（右鍵「更多工具與選項…」用）
- quickseemd run ID [--out 資料夾] [--result 結果.json] 檔案…
                                   不開介面直接執行工具或一鍵動作（對應原版 --qs-run）
- quickseemd tools [--list]        列出所有工具與動作代碼（對應原版 qs-tool --list）
- quickseemd convert …             萬用轉換（對應原版 qs-convert）
- quickseemd render 檔案… 輸出資料夾  把預覽輸出成 HTML（對應原版 qs-render）
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path


def _cmd_run(args: argparse.Namespace) -> int:
    from .core import registry  # noqa: PLC0415
    from .core.runner import JobSpec, run_spec  # noqa: PLC0415

    resolved = registry.resolve(args.id)
    if resolved is None:
        print(f"錯誤：找不到工具或動作：{args.id}", file=sys.stderr)
        return 2
    _tool, action_id = resolved
    out = None
    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
    files = [str(Path(f).expanduser().resolve()) for f in args.files]
    spec = JobSpec("action" if action_id else "tool", args.id, files, {}, str(out) if out else None)

    def report(fraction: float, message: str | None) -> None:
        pct = int(fraction * 100)
        sys.stderr.write(f"\r{pct:3d}% {message or ''}\x1b[K")
        sys.stderr.flush()

    result = run_spec(spec, report, threading.Event())
    sys.stderr.write("\n")
    payload = json.dumps(result.to_json(), ensure_ascii=False, indent=2, sort_keys=True)
    print(payload)
    if args.result:
        Path(args.result).expanduser().write_text(payload + "\n", "utf-8")
    return 0 if result.error is None else 1


def _cmd_tools(_args: argparse.Namespace) -> int:
    from .core import quickactions, registry  # noqa: PLC0415

    for t in registry.all_tools():
        print(f"{t.id}\t{t.title}\t[{t.input_label}]")
        for aid in sorted(a.id for a in quickactions.ACTIONS if a.tool_id == t.id and a.enabled):
            print(f"  ↳ {aid}")
    return 0


def _cmd_convert(argv: list[str]) -> int:
    from .core.converter import cli as convert_cli  # noqa: PLC0415

    return convert_cli.main(argv)


def _cmd_render(argv: list[str]) -> int:
    from .core.preview import cli as render_cli  # noqa: PLC0415

    return render_cli.main(argv)


def _launch_app(argv: list[str]) -> int:
    from .app.main import main as app_main  # noqa: PLC0415

    return app_main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "open":
        return _launch_app(argv)
    cmd, rest = argv[0], argv[1:]
    if cmd == "convert":
        return _cmd_convert(rest)
    if cmd == "render":
        return _cmd_render(rest)
    parser = argparse.ArgumentParser(prog="quickseemd")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="不開介面執行工具或一鍵動作")
    p_run.add_argument("id")
    p_run.add_argument("--out")
    p_run.add_argument("--result")
    p_run.add_argument("files", nargs="+")
    sub.add_parser("tools", help="列出工具與動作代碼").add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    return _cmd_tools(args)


if __name__ == "__main__":
    sys.exit(main())
