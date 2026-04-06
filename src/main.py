"""
OpenModelica Simulation Runner
================================
A PyQt6 desktop application to launch OpenModelica compiled executables
with configurable simulation parameters (start time, stop time).

Author: Rakesh
"""

import os
import sys
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QTextEdit, QFrame, QMessageBox,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QTextCursor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _openmodelica_bin_dirs() -> list[str]:
    """
    Return a list of candidate OpenModelica bin directories on Windows.

    OpenModelica installs its runtime DLLs (libOpenModelicaRuntimeC.dll,
    libomcgc.dll, sundials DLLs, etc.) inside its own bin/ folder.  When the
    compiled model executable is launched from a plain subprocess those DLLs
    are not on PATH, causing exit code 0xC0000135 (STATUS_DLL_NOT_FOUND).

    We probe the three most common install locations and return every one that
    actually exists.
    """
    candidates: list[str] = []

    # 1. Explicit environment variable set by the OM installer
    om_home = os.environ.get("OPENMODELICAHOME", "")
    if om_home:
        candidates.append(str(Path(om_home) / "bin"))

    # 2. Common default install paths on Windows
    for root in (
        Path("C:/Program Files/OpenModelica"),
        Path("C:/OpenModelica"),
    ):
        for child in sorted(root.glob("OpenModelica*"), reverse=True):
            candidates.append(str(child / "bin"))
        candidates.append(str(root / "bin"))

    return [p for p in candidates if Path(p).is_dir()]


def _build_env_with_om_path() -> dict[str, str]:
    """
    Return a copy of the current environment with OpenModelica bin dirs
    prepended to PATH so the simulation executable can find its DLLs.
    """
    env = os.environ.copy()
    extra = _openmodelica_bin_dirs()
    if extra:
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------------------
# Worker Thread
# ---------------------------------------------------------------------------

class SimulationWorker(QThread):
    """Runs the OpenModelica executable in a background thread."""

    output_ready = pyqtSignal(str)    # emitted for each stdout/stderr line
    finished_ok = pyqtSignal(int)     # emitted with the return code on exit
    error_occurred = pyqtSignal(str)  # emitted with a human-readable message

    def __init__(self, executable: str, start_time: int, stop_time: int):
        super().__init__()
        self.executable = executable
        self.start_time = start_time
        self.stop_time = stop_time

    def run(self) -> None:
        """
        Launch the OpenModelica executable with the -override flag and stream
        its combined stdout/stderr back to the UI via Qt signals.

        The working directory is the executable's parent folder so the exe can
        find sibling files (_init.xml, _info.json, etc.).
        PATH is extended with the OpenModelica bin directory to resolve DLLs
        (fixes exit code 0xC0000135 on Windows).
        """
        cmd = [
            self.executable,
            f"-startTime={self.start_time}",
            f"-stopTime={self.stop_time}",
        ]
        work_dir = str(Path(self.executable).parent)
        env = _build_env_with_om_path()

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=work_dir,
                env=env,
            )
            for line in process.stdout:
                self.output_ready.emit(line.rstrip())
            process.wait()
            self.finished_ok.emit(process.returncode)

        except FileNotFoundError:
            self.error_occurred.emit(
                f"Executable not found:\n{self.executable}"
            )
        except PermissionError:
            self.error_occurred.emit(
                "Permission denied. Make sure the file is executable."
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.error_occurred.emit(f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# Validated integer input widget
# ---------------------------------------------------------------------------

class IntInputField(QWidget):
    """A labelled integer input with inline validation feedback."""

    def __init__(self, label: str, placeholder: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.label = QLabel(label)
        self.label.setObjectName("fieldLabel")

        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText(placeholder)
        self.line_edit.setObjectName("intInput")

        self.error_label = QLabel("")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setVisible(False)

        layout.addWidget(self.label)
        layout.addWidget(self.line_edit)
        layout.addWidget(self.error_label)

    def value(self) -> str:
        """Return the current text, stripped of whitespace."""
        return self.line_edit.text().strip()

    def set_error(self, msg: str) -> None:
        """Display an inline error and highlight the input red."""
        self.error_label.setText(msg)
        self.error_label.setVisible(bool(msg))
        self.line_edit.setProperty("hasError", bool(msg))
        self.line_edit.style().unpolish(self.line_edit)
        self.line_edit.style().polish(self.line_edit)

    def clear_error(self) -> None:
        """Remove any displayed error."""
        self.set_error("")


# ---------------------------------------------------------------------------
# Main Application Window
# ---------------------------------------------------------------------------

class SimulationRunnerWindow(QMainWindow):
    """
    Main window of the OpenModelica Simulation Runner.

    Provides:
    - Executable file picker (Browse button + read-only path field)
    - Start time / Stop time integer inputs with live inline validation
    - Run button that launches the simulation in a background thread
    - Live scrolling console output
    - Stop button to terminate a running simulation
    - Status bar with animated indeterminate progress bar
    """

    # Constraint from the problem statement: 0 <= start < stop < 5
    TIME_MIN: int = 0
    TIME_MAX: int = 4  # stop time must be < 5

    def __init__(self):
        super().__init__()
        self._worker: SimulationWorker | None = None
        self._setup_window()
        self._build_ui()
        self._apply_stylesheet()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        self.setWindowTitle("OpenModelica Simulation Runner")
        self.setMinimumSize(780, 600)
        self.resize(920, 680)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._make_header())
        root_layout.addWidget(self._make_body(), stretch=1)
        root_layout.addWidget(self._make_footer())

    def _make_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(72)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(28, 0, 28, 0)

        icon_label = QLabel("⚙")
        icon_label.setObjectName("headerIcon")

        title = QLabel("OpenModelica Simulation Runner")
        title.setObjectName("headerTitle")

        subtitle = QLabel("FOSSEE · Two Connected Tanks")
        subtitle.setObjectName("headerSubtitle")

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addWidget(title)
        text_box.addWidget(subtitle)

        layout.addWidget(icon_label)
        layout.addSpacing(12)
        layout.addLayout(text_box)
        layout.addStretch()
        return header

    def _make_body(self) -> QWidget:
        body = QWidget()
        body.setObjectName("body")
        layout = QHBoxLayout(body)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(24)

        layout.addWidget(self._make_control_panel(), stretch=0)
        layout.addWidget(self._make_console_panel(), stretch=1)
        return body

    def _make_control_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("controlPanel")
        panel.setFixedWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(20)

        # Section title
        sec_label = QLabel("SIMULATION PARAMETERS")
        sec_label.setObjectName("sectionLabel")
        layout.addWidget(sec_label)

        # Executable picker
        exec_label = QLabel("Executable")
        exec_label.setObjectName("fieldLabel")
        layout.addWidget(exec_label)

        exe_row = QHBoxLayout()
        exe_row.setSpacing(8)

        self.exe_input = QLineEdit()
        self.exe_input.setPlaceholderText("Path to .exe …")
        self.exe_input.setObjectName("exeInput")
        self.exe_input.setReadOnly(True)

        browse_btn = QPushButton("Browse")
        browse_btn.setObjectName("browseBtn")
        browse_btn.setFixedWidth(72)
        browse_btn.clicked.connect(self._browse_executable)

        exe_row.addWidget(self.exe_input)
        exe_row.addWidget(browse_btn)
        layout.addLayout(exe_row)

        self.exe_error = QLabel("")
        self.exe_error.setObjectName("errorLabel")
        self.exe_error.setVisible(False)
        layout.addWidget(self.exe_error)

        # Time inputs
        self.start_field = IntInputField("Start Time (integer, >= 0)", "e.g.  0")
        self.stop_field = IntInputField("Stop Time (integer, < 5)", "e.g.  4")
        layout.addWidget(self.start_field)
        layout.addWidget(self.stop_field)

        hint = QLabel("Constraint:  0 <= start < stop < 5")
        hint.setObjectName("hintLabel")
        layout.addWidget(hint)

        layout.addStretch()

        # Run button
        self.run_btn = QPushButton("Run Simulation")
        self.run_btn.setObjectName("runBtn")
        self.run_btn.setFixedHeight(44)
        self.run_btn.clicked.connect(self._on_run_clicked)
        layout.addWidget(self.run_btn)

        # Stop button (hidden until a simulation is running)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self.stop_btn)

        return panel

    def _make_console_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("consolePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Console header bar
        console_header = QWidget()
        console_header.setObjectName("consoleHeader")
        console_header.setFixedHeight(36)
        ch_layout = QHBoxLayout(console_header)
        ch_layout.setContentsMargins(14, 0, 14, 0)

        console_title = QLabel("SIMULATION OUTPUT")
        console_title.setObjectName("consoleTitleLabel")

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedSize(54, 24)
        self.clear_btn.clicked.connect(self._clear_console)

        ch_layout.addWidget(console_title)
        ch_layout.addStretch()
        ch_layout.addWidget(self.clear_btn)

        # Output text area
        self.console = QTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        layout.addWidget(console_header)
        layout.addWidget(self.console, stretch=1)
        return panel

    def _make_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("footer")
        footer.setFixedHeight(36)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 0, 20, 0)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")

        self.progress = QProgressBar()
        self.progress.setObjectName("progressBar")
        self.progress.setRange(0, 0)   # indeterminate busy indicator
        self.progress.setFixedWidth(120)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)

        layout.addWidget(self.status_label)
        layout.addStretch()
        layout.addWidget(self.progress)
        return footer

    # ------------------------------------------------------------------
    # Stylesheet
    # NOTE: PyQt6 does not support the CSS 'overflow' property — omitted.
    # ------------------------------------------------------------------

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
        QMainWindow, QWidget {
            background: #0f1117;
            color: #e2e8f0;
            font-family: "Consolas", "Courier New", monospace;
            font-size: 13px;
        }

        #header {
            background: #161b27;
            border-bottom: 1px solid #2a2f3e;
        }
        #headerIcon  { font-size: 28px; color: #38bdf8; }
        #headerTitle {
            font-size: 17px; font-weight: 700;
            color: #f1f5f9; letter-spacing: 0.5px;
        }
        #headerSubtitle { font-size: 11px; color: #64748b; letter-spacing: 1.2px; }

        #body { background: #0f1117; }

        #controlPanel {
            background: #161b27;
            border: 1px solid #2a2f3e;
            border-radius: 10px;
        }
        #sectionLabel {
            font-size: 10px; letter-spacing: 1.8px;
            color: #38bdf8; font-weight: 700;
        }
        #fieldLabel  { font-size: 11px; color: #94a3b8; letter-spacing: 0.5px; }
        #hintLabel   { font-size: 11px; color: #475569; font-style: italic; }

        QLineEdit {
            background: #0f1117;
            border: 1px solid #2a2f3e;
            border-radius: 6px;
            padding: 7px 10px;
            color: #e2e8f0;
            selection-background-color: #38bdf8;
        }
        QLineEdit:focus        { border: 1px solid #38bdf8; }
        QLineEdit[hasError="true"] { border: 1px solid #f87171; }
        #exeInput              { color: #7dd3fc; }
        #errorLabel            { font-size: 11px; color: #f87171; }

        #browseBtn {
            background: #1e293b; border: 1px solid #334155;
            border-radius: 6px; color: #94a3b8; padding: 0;
        }
        #browseBtn:hover { background: #263548; color: #e2e8f0; }

        #runBtn {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #0ea5e9, stop:1 #38bdf8);
            border: none; border-radius: 8px;
            color: #0f1117; font-weight: 700;
            font-size: 13px; letter-spacing: 0.3px;
        }
        #runBtn:hover {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #38bdf8, stop:1 #7dd3fc);
        }
        #runBtn:disabled { background: #1e293b; color: #475569; }

        #stopBtn {
            background: #1e293b; border: 1px solid #f87171;
            border-radius: 6px; color: #f87171; font-size: 12px;
        }
        #stopBtn:hover { background: #2d1a1a; }

        #clearBtn {
            background: #1e293b; border: 1px solid #334155;
            border-radius: 4px; color: #64748b; font-size: 11px;
        }
        #clearBtn:hover { color: #e2e8f0; border-color: #475569; }

        #consolePanel {
            background: #0a0d14;
            border: 1px solid #2a2f3e;
            border-radius: 10px;
        }
        #consoleHeader {
            background: #161b27;
            border-bottom: 1px solid #2a2f3e;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
        }
        #consoleTitleLabel {
            font-size: 10px; letter-spacing: 1.8px;
            color: #38bdf8; font-weight: 700;
        }
        #console {
            background: #0a0d14; color: #a3e635;
            border: none;
            font-family: "Consolas", "Courier New", monospace;
            font-size: 12px; padding: 12px;
        }

        QScrollBar:vertical   { background: #0f1117; width: 8px;  border-radius: 4px; }
        QScrollBar:horizontal { background: #0f1117; height: 8px; }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #2a2f3e; border-radius: 4px;
        }

        #footer { background: #161b27; border-top: 1px solid #2a2f3e; }
        #statusLabel { font-size: 11px; color: #64748b; letter-spacing: 0.5px; }

        QProgressBar {
            background: #0f1117; border: none; border-radius: 3px;
        }
        QProgressBar::chunk { background: #38bdf8; border-radius: 3px; }
        """)

    # ------------------------------------------------------------------
    # Slot: browse for executable
    # ------------------------------------------------------------------

    def _browse_executable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select OpenModelica Executable",
            "",
            "Executable Files (*.exe);;All Files (*)",
        )
        if not path:
            return

        self.exe_input.setText(path)
        self.exe_error.setVisible(False)
        self._log(f"[info] Selected: {path}")

        # Report DLL path resolution status
        found = _openmodelica_bin_dirs()
        if found:
            self._log(f"[info] OM runtime DLLs located at: {found[0]}")
        else:
            self._log(
                "[warn] OpenModelica bin dir not auto-detected.\n"
                "       If the run fails with code -1073741515 (0xC0000135),\n"
                "       set OPENMODELICAHOME to your OM install folder, e.g.:\n"
                "         set OPENMODELICAHOME=C:\\OpenModelica1.24.0\n"
                "       Then restart this application."
            )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_inputs(self) -> tuple[bool, str, int, int]:
        """
        Validate the executable path and both time inputs.

        Returns
        -------
        tuple of (valid, exe_path, start_time, stop_time).
        When valid is False the numeric values should not be used.
        """
        valid = True
        exe_path = self.exe_input.text().strip()
        start_raw = self.start_field.value()
        stop_raw = self.stop_field.value()

        self.exe_error.setVisible(False)
        self.start_field.clear_error()
        self.stop_field.clear_error()

        # Executable
        if not exe_path:
            self.exe_error.setText("Please select an executable.")
            self.exe_error.setVisible(True)
            valid = False
        elif not Path(exe_path).is_file():
            self.exe_error.setText("File does not exist.")
            self.exe_error.setVisible(True)
            valid = False

        # Start time
        start_time = 0
        if not start_raw:
            self.start_field.set_error("Start time is required.")
            valid = False
        else:
            try:
                start_time = int(start_raw)
                if start_time < self.TIME_MIN:
                    self.start_field.set_error(f"Must be >= {self.TIME_MIN}.")
                    valid = False
            except ValueError:
                self.start_field.set_error("Must be an integer.")
                valid = False

        # Stop time
        stop_time = 0
        if not stop_raw:
            self.stop_field.set_error("Stop time is required.")
            valid = False
        else:
            try:
                stop_time = int(stop_raw)
                if stop_time > self.TIME_MAX:
                    self.stop_field.set_error(f"Must be < {self.TIME_MAX + 1}.")
                    valid = False
            except ValueError:
                self.stop_field.set_error("Must be an integer.")
                valid = False

        # Cross-field: start must be strictly less than stop
        if valid and start_time >= stop_time:
            self.start_field.set_error("Start must be less than stop.")
            valid = False

        return valid, exe_path, start_time, stop_time

    # ------------------------------------------------------------------
    # Slot: run simulation
    # ------------------------------------------------------------------

    def _on_run_clicked(self) -> None:
        valid, exe_path, start_time, stop_time = self._validate_inputs()
        if not valid:
            return

        self._set_running(True)
        self._log(
            f"[start] Launching simulation ...\n"
            f"         Executable : {exe_path}\n"
            f"         Start time : {start_time}\n"
            f"         Stop time  : {stop_time}"
        )

        self._worker = SimulationWorker(exe_path, start_time, stop_time)
        self._worker.output_ready.connect(self._on_output)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------
    # Slot: stop simulation
    # ------------------------------------------------------------------

    def _on_stop_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._log("[warn] Simulation terminated by user.")
            self._set_running(False)

    # ------------------------------------------------------------------
    # Worker signal callbacks
    # ------------------------------------------------------------------

    def _on_output(self, line: str) -> None:
        self._log(line)

    def _on_finished(self, return_code: int) -> None:
        if return_code == 0:
            self._log("[done] Simulation completed successfully.")
            self._set_status("Simulation finished", "#a3e635")
        else:
            hex_code = hex(return_code & 0xFFFFFFFF)
            self._log(f"[warn] Process exited with code {return_code} ({hex_code}).")
            if return_code == -1073741515:  # 0xC0000135
                self._log(
                    "[hint] 0xC0000135 = missing DLL.\n"
                    "       Set OPENMODELICAHOME to your OM install folder\n"
                    "       and restart this app so DLLs can be found."
                )
            self._set_status(f"Exited {hex_code}", "#fbbf24")
        self._set_running(False)

    def _on_error(self, message: str) -> None:
        self._log(f"[error] {message}")
        self._set_status("Error - see console", "#f87171")
        self._set_running(False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self, text: str) -> None:
        """Append text to the console and auto-scroll to the bottom."""
        self.console.append(text)
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.console.setTextCursor(cursor)

    def _clear_console(self) -> None:
        self.console.clear()

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.stop_btn.setVisible(running)
        self.progress.setVisible(running)
        if running:
            self._set_status("Running ...", "#38bdf8")
        else:
            QTimer.singleShot(4000, lambda: self._set_status("Ready"))

    def _set_status(self, msg: str, color: str = "#64748b") -> None:
        self.status_label.setText(msg)
        self.status_label.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------
    # Window close guard
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Simulation Running",
                "A simulation is still running.\nTerminate and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._worker.terminate()
                self._worker.wait()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OpenModelica Simulation Runner")
    app.setApplicationVersion("1.0.0")

    window = SimulationRunnerWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()