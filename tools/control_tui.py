"""Textual control panel for the local Consultorios Compartidos app."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import subprocess
import webbrowser
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, RichLog, Static

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_URL = os.environ.get("CONSULTORIOS_TUI_URL", "http://127.0.0.1:8000/")
RUNSERVER_BIND = os.environ.get("CONSULTORIOS_TUI_BIND", "127.0.0.1:8000")
HOST, PORT_TEXT = RUNSERVER_BIND.rsplit(":", maxsplit=1)
PORT = int(PORT_TEXT)


@dataclass(frozen=True)
class CommandSpec:
    title: str
    argv: tuple[str, ...]


class ConsultoriosControlTUI(App[None]):
    """Local operations TUI for build, run, browse and shutdown."""

    CSS = """
    Screen {
        background: #071017;
        color: #e6eef6;
    }

    Header {
        background: #0f2137;
        color: #f5f7fb;
    }

    #title {
        height: 3;
        content-align: center middle;
        text-style: bold;
        background: #10243c;
        color: #ffffff;
    }

    #status {
        height: 1;
        content-align: center middle;
        color: #52d273;
        background: #071017;
    }

    #body {
        height: 1fr;
        border-top: solid #263847;
    }

    #actions {
        width: 29;
        padding: 2 2;
        border-right: solid #263847;
    }

    #log-panel {
        width: 1fr;
        padding: 2 2;
    }

    #log-title {
        height: 2;
        text-style: bold;
    }

    Button {
        width: 100%;
        height: 3;
        margin-bottom: 1;
        text-style: bold;
    }

    #prepare {
        background: #238bd0;
    }

    #build {
        background: #ffb13b;
        color: #08111a;
    }

    #start {
        background: #4bc96f;
        color: #08111a;
    }

    #reload {
        background: #6f8cff;
    }

    #browser {
        background: #238bd0;
    }

    #stop {
        background: #c43865;
    }

    #quit {
        background: #24394c;
    }

    RichLog {
        height: 1fr;
        background: #02070c;
        border: round #263847;
        padding: 1 2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Cerrar"),
        ("r", "refresh_status", "Actualizar estado"),
        ("d", "restart_server", "Recargar Django"),
        ("p", "toggle_dark", "Palette"),
    ]

    server_process: asyncio.subprocess.Process | None = None
    server_log_task: asyncio.Task[None] | None = None
    busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Control de Consultorios Compartidos", id="title")
        yield Static("Estado: verificando...", id="status")
        with Horizontal(id="body"):
            with Vertical(id="actions"):
                yield Button("Preparar entorno", id="prepare", variant="primary")
                yield Button("Construir aplicación", id="build", variant="warning")
                yield Button("Levantar", id="start", variant="success")
                yield Button("Recargar Django", id="reload", variant="primary")
                yield Button("Abrir navegador", id="browser", variant="primary")
                yield Button("Apagar", id="stop", variant="error")
                yield Button("Cerrar", id="quit", variant="default")
            with Vertical(id="log-panel"):
                yield Static("Logs", id="log-title")
                yield RichLog(id="logs", markup=True, wrap=True, auto_scroll=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "Control de Consultorios Compartidos - Local"
        self.sub_title = APP_URL
        self.log_line("TUI listo para operar el proyecto local.", "cyan")
        self.log_line(f"Workspace: {ROOT_DIR}", "white")
        self.log_line(f"URL: {APP_URL}", "white")
        self.set_interval(2.0, self.refresh_status)
        await self.refresh_status()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "prepare":
            await self.run_sequence(
                "Preparar entorno",
                [CommandSpec("uv sync --group dev", ("uv", "sync", "--group", "dev"))],
            )
        elif button_id == "build":
            await self.run_sequence(
                "Construir aplicación",
                [
                    CommandSpec(
                        "Aplicar migraciones",
                        ("uv", "run", "python", "manage.py", "migrate"),
                    ),
                    CommandSpec(
                        "Verificar Django",
                        ("uv", "run", "python", "manage.py", "check"),
                    ),
                    CommandSpec(
                        "Verificar migraciones",
                        (
                            "uv",
                            "run",
                            "python",
                            "manage.py",
                            "makemigrations",
                            "--check",
                            "--dry-run",
                        ),
                    ),
                ],
            )
        elif button_id == "start":
            await self.start_server()
        elif button_id == "reload":
            await self.restart_server()
        elif button_id == "browser":
            self.open_browser()
        elif button_id == "stop":
            await self.stop_server_or_port_owner()
        elif button_id == "quit":
            await self.action_quit()

    async def run_sequence(
        self,
        title: str,
        commands: Iterable[CommandSpec],
    ) -> None:
        if self.busy:
            self.log_line("Ya hay una operación en curso.", "yellow")
            return

        self.busy = True
        self.set_action_buttons_disabled(True)
        self.log_line(f"Iniciando: {title}", "cyan")
        try:
            for command in commands:
                return_code = await self.run_command(command)
                if return_code != 0:
                    self.log_line(
                        f"Operación detenida por error en: {command.title}",
                        "red",
                    )
                    return
            self.log_line("Comando completado.", "green")
        finally:
            self.busy = False
            self.set_action_buttons_disabled(False)
            await self.refresh_status()

    async def run_command(self, command: CommandSpec) -> int:
        self.log_line(f"$ {' '.join(command.argv)}", "bright_cyan")
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=ROOT_DIR,
            env=self.command_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        async for raw_line in process.stdout:
            self.log_line(raw_line.decode(errors="replace").rstrip(), "white")
        return_code = await process.wait()
        color = "green" if return_code == 0 else "red"
        self.log_line(f"Salida: {return_code}", color)
        return return_code

    async def start_server(self) -> None:
        if self.server_process and self.server_process.returncode is None:
            self.log_line("La aplicación ya está levantada por este TUI.", "yellow")
            return
        if is_port_open(HOST, PORT):
            self.log_line(
                f"El puerto {RUNSERVER_BIND} ya responde. No inicio otro servidor.",
                "yellow",
            )
            await self.refresh_status()
            return

        self.log_line("Levantando Django local...", "cyan")
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        self.server_process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "python",
            "manage.py",
            "runserver",
            RUNSERVER_BIND,
            cwd=ROOT_DIR,
            env=self.command_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **kwargs,
        )
        self.server_log_task = asyncio.create_task(self.stream_server_logs())
        self.log_line(f"Servidor iniciado en {APP_URL}", "green")
        await self.refresh_status()

    async def stream_server_logs(self) -> None:
        process = self.server_process
        if process is None or process.stdout is None:
            return
        async for raw_line in process.stdout:
            self.log_line(raw_line.decode(errors="replace").rstrip(), "white")
        return_code = await process.wait()
        self.log_line(f"Servidor detenido. Salida: {return_code}", "yellow")
        self.server_process = None
        await self.refresh_status()

    def open_browser(self) -> None:
        webbrowser.open(APP_URL)
        self.log_line(f"Navegador abierto en {APP_URL}", "green")

    async def stop_server(self) -> None:
        process = self.server_process
        if process is None or process.returncode is not None:
            self.log_line("No hay servidor iniciado por este TUI.", "yellow")
            await self.refresh_status()
            return

        self.log_line("Apagando aplicación...", "magenta")
        await terminate_process(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=8)
        except TimeoutError:
            self.log_line("Forzando apagado del proceso.", "red")
            process.kill()
            await process.wait()
        self.server_process = None
        await self.refresh_status()

    async def restart_server(self) -> None:
        if self.busy:
            self.log_line("Ya hay una operación en curso.", "yellow")
            return

        self.busy = True
        self.set_action_buttons_disabled(True)
        self.log_line("Recargando Django para tomar cambios de código...", "cyan")
        try:
            await self.stop_server_or_port_owner()
            if not await wait_for_port_closed(HOST, PORT):
                self.log_line(
                    "El puerto "
                    f"{RUNSERVER_BIND} sigue ocupado; no levanté otro servidor.",
                    "red",
                )
                return
            await self.start_server()
        finally:
            self.busy = False
            self.set_action_buttons_disabled(False)
            await self.refresh_status()

    async def stop_server_or_port_owner(self) -> None:
        process = self.server_process
        if process and process.returncode is None:
            await self.stop_server()
            return

        if not is_port_open(HOST, PORT):
            self.log_line("El puerto ya está libre.", "green")
            return

        pids = find_listening_pids(PORT)
        if not pids:
            self.log_line(
                f"El puerto {RUNSERVER_BIND} está ocupado, pero no pude ubicar el PID.",
                "red",
            )
            return

        self.log_line(
            f"Liberando puerto {RUNSERVER_BIND}: PID(s) {', '.join(map(str, pids))}",
            "magenta",
        )
        for pid in pids:
            result = terminate_pid(pid)
            color = "green" if result.returncode == 0 else "red"
            output = (result.stdout or result.stderr or "").strip()
            self.log_line(f"PID {pid}: salida {result.returncode}", color)
            if output:
                self.log_line(output, color)

    async def refresh_status(self) -> None:
        status = self.query_one("#status", Static)
        own_server = self.server_process and self.server_process.returncode is None
        port_ready = is_port_open(HOST, PORT)
        if own_server:
            status.update("Estado: encendido")
            status.styles.color = "#52d273"
        elif port_ready:
            status.update("Estado: puerto ocupado / app externa detectada")
            status.styles.color = "#ffcf5a"
        else:
            status.update("Estado: apagado")
            status.styles.color = "#cdd6df"

    async def action_refresh_status(self) -> None:
        await self.refresh_status()
        self.log_line("Estado actualizado.", "cyan")

    async def action_restart_server(self) -> None:
        await self.restart_server()

    async def action_quit(self) -> None:
        await self.stop_server()
        self.exit()

    def set_action_buttons_disabled(self, disabled: bool) -> None:
        for button_id in ("prepare", "build", "start", "reload"):
            self.query_one(f"#{button_id}", Button).disabled = disabled

    def command_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("DJANGO_DEBUG", "true")
        env.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    def log_line(self, message: str, color: str = "white") -> None:
        logs = self.query_one("#logs", RichLog)
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = Text()
        text.append(timestamp, style="bold green")
        text.append("  ")
        text.append(message, style=color)
        logs.write(text)


def is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


async def wait_for_port_closed(host: str, port: int, timeout: float = 8.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not is_port_open(host, port):
            return True
        await asyncio.sleep(0.2)
    return False


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if os.name == "nt":
        subprocess.run(
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),
            capture_output=True,
            text=True,
            check=False,
        )
        return

    killpg = getattr(os, "killpg", None)
    if not callable(killpg):
        process.terminate()
        return

    try:
        killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()


def find_listening_pids(port: int) -> list[int]:
    if os.name == "nt":
        return find_windows_listening_pids(port)
    return find_unix_listening_pids(port)


def find_windows_listening_pids(port: int) -> list[int]:
    result = subprocess.run(
        ("netstat", "-ano", "-p", "tcp"),
        capture_output=True,
        text=True,
        check=False,
    )
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[3].upper()
        pid_text = parts[4]
        if state != "LISTENING" or not local_address_matches_port(local_address, port):
            continue
        try:
            pids.add(int(pid_text))
        except ValueError:
            continue
    return sorted(pids)


def find_unix_listening_pids(port: int) -> list[int]:
    result = subprocess.run(
        ("lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"),
        capture_output=True,
        text=True,
        check=False,
    )
    pids: set[int] = set()
    for pid_text in result.stdout.splitlines():
        try:
            pids.add(int(pid_text.strip()))
        except ValueError:
            continue
    return sorted(pids)


def local_address_matches_port(local_address: str, port: int) -> bool:
    return local_address.endswith(f":{port}") or local_address.endswith(f".{port}")


def terminate_pid(pid: int) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        return subprocess.run(
            ("taskkill", "/PID", str(pid), "/T", "/F"),
            capture_output=True,
            text=True,
            check=False,
        )

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return subprocess.CompletedProcess(("kill", str(pid)), 0, "", "")
    except OSError as exc:
        return subprocess.CompletedProcess(("kill", str(pid)), 1, "", str(exc))
    return subprocess.CompletedProcess(("kill", str(pid)), 0, "", "")


def smoke_test() -> int:
    missing = [
        path
        for path in (ROOT_DIR / "manage.py", ROOT_DIR / "pyproject.toml")
        if not path.exists()
    ]
    if missing:
        print(f"Archivos requeridos no encontrados: {missing}")
        return 1
    print("Smoke TUI OK")
    print(f"Workspace: {ROOT_DIR}")
    print(f"URL: {APP_URL}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Valida imports y configuración básica sin abrir la TUI.",
    )
    args = parser.parse_args()
    if args.smoke_test:
        return smoke_test()
    ConsultoriosControlTUI().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
