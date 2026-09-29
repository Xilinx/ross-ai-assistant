#!/usr/bin/env python3

# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""
Run a pre-compiled model on a board over SSH

Transport uses the `fabric` (paramiko) library:
    pip install fabric
"""

import argparse
import os
import shlex
import shutil
import sys
import tarfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ssh_utils  # noqa: E402

# These are the only debug knobs the board run relies on.
BOARD_ENV = {
    "DEBUG_VAIML_PARTITION": "1",
    "FXML_LOG_LEVEL": "1",
    "FLEXML_PRINT_VITISTOOLS_OUTPUT": "1",
}


def stage_scripts(
    example_path: Path, remote_dir: Path, run_args: list, pre_run_cmd: str = ""
) -> Path:
    """Copy the scripts into <project>/scripts and write board_exec_cmd.sh."""
    script_dir = Path(__file__).parent
    local_scripts_dir = example_path / "scripts"
    shutil.copytree(
        script_dir,
        local_scripts_dir,
        dirs_exist_ok=True,
    )

    run_args_str = " ".join(shlex.quote(a) for a in run_args) if run_args else ""
    exports = "\n".join(f"export {k}={v}" for k, v in BOARD_ENV.items())
    remote_exec_cmd = f"""\
set -e
[ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh

{exports}

cd {remote_dir}
{pre_run_cmd}
python scripts/run.py {run_args_str}
"""
    exec_path = local_scripts_dir / "board_exec_cmd.sh"
    with open(exec_path, "w") as f:
        f.write(remote_exec_cmd)
    os.chmod(exec_path, 0o755)
    return local_scripts_dir


def pack_dir(src_dir: Path, tgz_path: Path, exclude=()):
    """tar czf tgz_path -C src_dir .  (stdlib, no `tar` binary)."""
    exclude = set(exclude)
    with tarfile.open(tgz_path, "w:gz") as tar:
        for item in sorted(src_dir.iterdir()):
            if item.name in exclude:
                continue
            tar.add(item, arcname=item.name)


def unpack_dir(tgz_path: Path, dest_dir: Path):
    """tar xzf tgz_path -C dest_dir  (stdlib, no `tar` binary)."""
    with tarfile.open(tgz_path, "r:gz") as tar:
        # filter="data" (py3.12+) strips unsafe members; ignore on older pythons.
        try:
            tar.extractall(dest_dir, filter="data")
        except TypeError:
            tar.extractall(dest_dir)


def run_on_board(
    project_path,
    boardhost,
    *,
    board_dir=Path("/tmp"),
    board_user="amd-edf",
    board_password=None,
    board_key=None,
    ssh_port=22,
    timeout=60.0,
    run_args=None,
    pre_run_cmd="",
    dry_run=False,
    keep_archives=False,
):
    """Copy a project to a board, run it, and copy the results back.

    Importable entry point mirroring the command-line interface. Returns the
    board run's exit code (0 on success, or on dry runs).
    """
    if not boardhost:
        raise ValueError("boardhost is required")

    # Flush prints on every newline so our status lines interleave correctly
    # with subprocess output (which writes straight to the fd) instead of all
    # appearing buffered at process exit.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    run_args = list(run_args or [])
    example_path = Path(project_path).resolve()
    board_dir = Path(board_dir)

    remote_dir = board_dir / example_path.name
    rq = shlex.quote(str(remote_dir))
    remote_tgz = shlex.quote(f"{board_dir}/{example_path.name}.xfer.tgz")

    auth = "password" if board_password else "key/agent"
    print(f"Target board: {board_user}@{boardhost}:{ssh_port} ({auth})")
    print(f"Board directory: {remote_dir}")

    stage_scripts(example_path, remote_dir, run_args, pre_run_cmd)

    if dry_run:
        print("Dry run: would connect, push project, run, and pull results.")
        return 0

    backend = ssh_utils.make_backend(
        boardhost,
        board_user,
        ssh_port,
        board_password,
        timeout,
        board_key,
    )

    xfer_dir = example_path / "xfer"
    xfer_dir.mkdir(parents=True, exist_ok=True)
    try:
        print("Copying project to board...")
        backend.run(f"rm -rf {rq} {remote_tgz} && mkdir -p {rq}", quiet=True)
        local_tgz = xfer_dir / "project.tgz"
        pack_dir(example_path, local_tgz, exclude={xfer_dir.name})
        backend.put(local_tgz, remote_tgz.strip("'"))
        backend.run(f"tar xzf {remote_tgz} -C {rq} && rm -f {remote_tgz}", quiet=True)

        print("Running on board:")
        rc_run = backend.run(f"cd {rq} && bash scripts/board_exec_cmd.sh", check=False)
        print(f"\n[board run exit code: {rc_run}]")

        print("Copying results back to host...")
        backend.run(f"tar czf {remote_tgz} -C {rq} .", quiet=True)
        result_tgz = xfer_dir / "result.tgz"
        backend.get(remote_tgz.strip("'"), result_tgz)
        backend.run(f"rm -rf {remote_tgz} {rq}", quiet=True)
        unpack_dir(result_tgz, example_path)

        return rc_run
    finally:
        backend.close()
        if not keep_archives:
            shutil.rmtree(xfer_dir, ignore_errors=True)


def parse_args(script_args):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=textwrap.dedent(__doc__ or "").strip(),
    )
    parser.add_argument(
        "-p", "--project-path", default=os.getcwd(), help="Path to project directory"
    )
    parser.add_argument(
        "--board-dir",
        type=Path,
        default=Path("/tmp"),
        help="Directory on the board under which the project is copied and run",
    )
    parser.add_argument(
        "--boardhost",
        default=os.environ.get("BOARDHOST"),
        help="Hostname of the board to use (required)",
    )
    parser.add_argument(
        "--board-user",
        default=os.environ.get("BOARD_USER", "amd-edf"),
        help="User to log in to the board as",
    )
    parser.add_argument(
        "--board-password",
        default=os.environ.get("BOARD_PASSWORD"),
        help="Password for the board (if unset, key/agent auth is used)",
    )
    parser.add_argument(
        "--board-key",
        type=Path,
        default=os.environ.get("BOARD_KEY"),
        help="Path to a private key file for board login (used instead of a password)",
    )
    parser.add_argument(
        "--ssh-port", type=int, default=22, help="SSH port on the board"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="TCP connect timeout (seconds)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be done, do not contact the board",
    )
    parser.add_argument(
        "--keep-archives",
        action="store_true",
        help="Keep the transfer archives in the xfer/ subdirectory instead of deleting them",
    )
    parser.add_argument(
        "--pre-run-cmd",
        default="",
        help="Shell snippet to run on the board just before the model "
        "(e.g. sourcing a virtual environment)",
    )

    args = parser.parse_args(script_args)
    if not args.boardhost:
        parser.error("--boardhost is required (or set the BOARDHOST env var)")
    return args


def main():
    # Split args at -- to separate script args from run.py args.
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        script_args = sys.argv[1:idx]
        run_args = sys.argv[idx + 1 :]
    else:
        script_args = sys.argv[1:]
        run_args = []

    args = parse_args(script_args)

    rc = run_on_board(
        args.project_path,
        args.boardhost,
        board_dir=args.board_dir,
        board_user=args.board_user,
        board_password=args.board_password,
        board_key=args.board_key,
        ssh_port=args.ssh_port,
        timeout=args.timeout,
        run_args=run_args,
        pre_run_cmd=args.pre_run_cmd,
        dry_run=args.dry_run,
        keep_archives=args.keep_archives,
    )
    if rc:
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
