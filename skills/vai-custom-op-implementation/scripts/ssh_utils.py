# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""SSH/SFTP transport helpers shared by the board-run scripts.

Transport uses the `fabric` (paramiko) library:
    pip install fabric
"""

import subprocess


class SubprocessHold:
    """A long-lived local process that holds a resource.

    The wrapped process is expected to block reading its stdin; closing that
    stdin (EOF) lets it exit and drop whatever it was holding. Used for the
    async board-lock holder. `readline` returns "" at EOF.
    """

    def __init__(self, proc):
        self._proc = proc

    def readline(self) -> str:
        assert self._proc.stdout is not None
        return self._proc.stdout.readline()

    def release(self) -> None:
        proc = self._proc
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            print("Lock holder did not exit; terminating it.", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


class _FabricHold:
    """Hold handle backed by a raw paramiko channel (see FabricBackend.start_hold).

    Mirrors SubprocessHold: `readline` returns "" at EOF; `release` shuts the
    write side (EOF to the remote command) and waits for it to exit.
    """

    def __init__(self, channel):
        self._channel = channel
        self._buf = ""
        self._eof = False

    def readline(self) -> str:
        while "\n" not in self._buf:
            if self._eof:
                line, self._buf = self._buf, ""
                return line
            data = self._channel.recv(4096)
            if not data:
                self._eof = True
                continue
            self._buf += data.decode("utf-8", "replace")
        line, _, self._buf = self._buf.partition("\n")
        return line + "\n"

    def release(self) -> None:
        try:
            self._channel.shutdown_write()
        except Exception:
            pass
        try:
            self._channel.recv_exit_status()
        except Exception:
            pass
        try:
            self._channel.close()
        except Exception:
            pass


class FabricBackend:
    """Transport using the `fabric` (paramiko) library."""

    def __init__(self, host, user, port, password, timeout, key=None):
        from fabric import Connection

        connect_kwargs = {}
        if key:
            connect_kwargs["key_filename"] = str(key)
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        elif password:
            connect_kwargs["password"] = password
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        self.conn = Connection(
            host=host,
            user=user,
            port=port,
            connect_timeout=timeout,
            connect_kwargs=connect_kwargs,
        )

    def run(self, command, *, check=True, quiet=False):
        print(f"Running command over SSH: {command}", flush=True)
        result = self.conn.run(command, hide=quiet, warn=not check, pty=False)
        print(f"Finished command over SSH (exit {result.exited})", flush=True)
        return result.exited

    def run_capture(self, command):
        """Run command and return its stdout as text (raises on failure)."""
        print(f"Running command over SSH: {command}", flush=True)
        result = self.conn.run(command, hide=True, pty=False)
        print(f"Finished command over SSH (exit {result.exited})", flush=True)
        return result.stdout

    def run_stream(self, command, *, server_alive=False):
        """Run command, streaming output live, and return the captured output.

        Fabric keeps stdout and stderr separate, so we concatenate both into the
        returned text. Some commands write the lines we parse to
        stderr, so returning stdout alone would drop them.
        """
        print(f"Running command over SSH: {command}", flush=True)
        result = self.conn.run(command, hide=False, pty=False)
        print(f"Finished command over SSH (exit {result.exited})", flush=True)
        return (result.stdout or "") + (result.stderr or "")

    def start_hold(self, command, *, server_alive=False) -> _FabricHold:
        """Start command on a raw channel with an open write side (see _FabricHold)."""
        self.conn.open()
        transport = self.conn.client.get_transport()
        channel = transport.open_session()
        # Merge stderr into the read stream so `recv` sees everything.
        channel.set_combine_stderr(True)
        channel.exec_command(command)
        return _FabricHold(channel)

    def put(self, local, remote):
        print(f"Copying file to board: {local} -> {remote}", flush=True)
        self.conn.put(str(local), remote=str(remote))
        print("Finished copying file to board", flush=True)

    def get(self, remote, local):
        print(f"Copying file from board: {remote} -> {local}", flush=True)
        self.conn.get(str(remote), local=str(local))
        print("Finished copying file from board", flush=True)

    def close(self):
        self.conn.close()


def make_backend(boardhost, board_user, ssh_port, board_password, timeout, board_key):
    """Build a Fabric transport backend for talking to the board."""
    try:
        return FabricBackend(
            boardhost, board_user, ssh_port, board_password, timeout, key=board_key
        )
    except ImportError:
        raise SystemExit("fabric is not available (pip install fabric)")


def make_boardhost_backend(
    boardhost,
    *,
    user=None,
    password=None,
    key=None,
    port=None,
    timeout=None,
):
    """Build a Fabric backend for talking to the *boardhost* itself.

    With no user/password/key given, Fabric reaches the boardhost using the
    caller's ssh config. An explicit user/password/key (e.g. from the
    BOARD_USER/BOARD_PASSWORD/BOARD_KEY env vars) is honored when provided.
    """
    try:
        return FabricBackend(boardhost, user, port or 22, password, timeout, key=key)
    except ImportError:
        raise SystemExit("fabric is not available (pip install fabric)")
