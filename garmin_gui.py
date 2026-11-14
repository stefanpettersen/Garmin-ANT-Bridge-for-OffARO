#!/usr/bin/env python3
"""Garmin ANT Bridge v0.6.5 diagnostic GUI (PySide6)."""
import json
import re
import os
import sys
from pathlib import Path
from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QPlainTextEdit, QMessageBox, QSpinBox, QCheckBox, QGroupBox, QHeaderView)

ROOT = Path(__file__).resolve().parent
PAIRING = ROOT / "garmin_pairing.json"
SCAN_RESULT = ROOT / "scan_results.json"
STOP_REQUEST = ROOT / ".garmin_gui_stop.request"
SENDER_LINE = re.compile(r"^\[SCAN\] ANT device (\d+) \| type=(\d+|\?) \| tx=(\d+|\?) \| source=(.*)$")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Garmin ANT+ Bridge for OffARO — v0.6.5 Search Timeout Diagnostics")
        self.resize(940, 680)
        self._output_pending = ""
        self._stop_requested = False
        self._usb_timeout_count = 0
        self._usb_backend_notice = False
        self._raw_output_file = None
        self.proc = None
        self.mode = None
        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        self.status = QLabel("Ready — no ANT connection")
        layout.addWidget(self.status)
        settings = QHBoxLayout()
        settings.addWidget(QLabel("Scan duration (s):"))
        self.duration = QSpinBox(); self.duration.setRange(10, 300); self.duration.setValue(45)
        settings.addWidget(self.duration)
        self.offaro = QCheckBox("Send to OffARO while tracking"); self.offaro.setChecked(True)
        settings.addWidget(self.offaro)
        self.diagnostics = QCheckBox("Capture ANT diagnostics (JSONL)")
        self.diagnostics.setChecked(True)
        self.show_details = QCheckBox("Show verbose diagnostics in GUI")
        self.show_details.setChecked(False)
        settings.addWidget(self.show_details)
        settings.addWidget(self.diagnostics)
        settings.addStretch()
        layout.addLayout(settings)
        buttons = QHBoxLayout()
        self.scan_btn = QPushButton("Scan Garmin senders")
        self.scan_btn.clicked.connect(self.start_scan)
        self.track_btn = QPushButton("Start tracking")
        self.track_btn.clicked.connect(self.start_tracking)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_process)
        self.stop_btn.setEnabled(False)
        for b in (self.scan_btn, self.track_btn, self.stop_btn):buttons.addWidget(b)
        layout.addLayout(buttons)
        group = QGroupBox("Detected Garmin senders — select one and click Pair")
        g = QVBoxLayout(group)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Garmin menu ID (calculated)", "ANT device number", "Device type", "Transmission type"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        g.addWidget(self.table)
        self.table.itemSelectionChanged.connect(self.refresh_buttons)
        self.pair_btn = QPushButton("Pair selected sender")
        self.pair_btn.clicked.connect(self.pair_selected)
        g.addWidget(self.pair_btn)
        layout.addWidget(group)
        self.paired = QLabel("Paired sender: none")
        layout.addWidget(self.paired)
        layout.addWidget(QLabel("Live diagnostics / bridge output:"))
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(2500)
        layout.addWidget(self.log, stretch=1)
        self.load_pairing()
        self.refresh_buttons()

    def append(self, line):
        self.log.appendPlainText(line.rstrip())
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def load_pairing(self):
        try:
            d = json.loads(PAIRING.read_text(encoding="utf-8"))
            ant = int(d["ant_device_number"])
            tx = d.get("transmission_type")
            garmin = d.get("garmin_menu_id")
            self.paired.setText(f"Paired: Garmin {garmin or '?'} | ANT {ant} | TX {tx if tx is not None else '?'}")
            return d
        except (FileNotFoundError, ValueError, KeyError, OSError):
            self.paired.setText("Paired sender: none")
            return None

    def refresh_buttons(self):
        running = self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning
        self.scan_btn.setEnabled(not running)
        self.track_btn.setEnabled(not running and self.load_pairing() is not None)
        self.pair_btn.setEnabled(self.table.currentRow() >= 0 and (not running or self.mode == "scan"))
        self.stop_btn.setEnabled(running)

    def launch(self, mode, args):
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        self.mode = mode
        self._output_pending = ""
        self._stop_requested = False
        try:
            STOP_REQUEST.unlink(missing_ok=True)
        except OSError as exc:
            self.append(f"[PROCESS] Cannot clear stop request: {exc}")
            return
        # Preserve exact backend output for debugging, even when GUI condenses it.
        try:
            raw_path = ROOT / "logs" / "bridge-console-raw.log"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_output_file = raw_path.open("a", encoding="utf-8")
            self._raw_output_file.write(f"\n=== {mode.upper()} START ===\n")
        except OSError as exc:
            self._raw_output_file = None
            self.append(f"[LOG] Could not open raw console log: {exc}")
        self._usb_timeout_count = 0
        self._usb_backend_notice = False
        self.proc = QProcess(self)
        env = self.proc.processEnvironment()
        # QProcess default environment may be empty: use the system environment.
        from PySide6.QtCore import QProcessEnvironment
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_output)
        self.proc.errorOccurred.connect(self.process_error)
        self.proc.finished.connect(self.process_finished)
        self.status.setText(f"{mode.capitalize()} running")
        self.append(f"\n=== {mode.upper()} START ===")
        self.proc.start(sys.executable, ["-u", str(ROOT / "garmin_dog_bridge.py"), *args])
        self.refresh_buttons()

    def start_scan(self):
        try:SCAN_RESULT.unlink(missing_ok=True)
        except OSError:pass
        self.table.setRowCount(0)
        self.launch("scan", ["--scan", "--scan-duration", str(self.duration.value()),
                             "--scan-json", str(SCAN_RESULT), "--stop-file", str(STOP_REQUEST)])

    def start_tracking(self):
        if not self.load_pairing():
            QMessageBox.warning(self, "No pairing", "Scan and pair a Garmin first.")
            return
        args = [] if self.offaro.isChecked() else ["--no-offaro"]
        if self.diagnostics.isChecked():
            args.extend(["--diagnostics", "--diagnostics-file", "logs/ant-diagnostics.jsonl"])
        self.launch("tracking", [*args, "--stop-file", str(STOP_REQUEST)])

    def process_error(self, error):
        if self._stop_requested and error == QProcess.ProcessError.Crashed:
            self.append("[PROCESS] Process did not finish gracefully")
        else:
            self.append(f"[PROCESS] Error: {error}")

    def read_output(self):
        if not self.proc:
            return
        self._output_pending += bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._output_pending:
            line, self._output_pending = self._output_pending.split("\n", 1)
            line = line.rstrip("\r")
            if self._raw_output_file is not None:
                self._raw_output_file.write(line + "\n")
            if "not implemented in usb backend" in line and ("kernel driver" in line):
                if not self._usb_backend_notice:
                    self.append("[USB] Windows backend does not support kernel-driver checks (informational; details in raw log)")
                    self._usb_backend_notice = True
            elif "libusb0-dll:err [_usb_reap_async] timeout error" in line:
                self._usb_timeout_count += 1
                if self._usb_timeout_count == 1:
                    self.append("[USB] Read timeout during ANT interruption; monitoring recovery (details in raw log)")
                elif self._usb_timeout_count in (5, 20, 100):
                    self.append(f"[USB] {self._usb_timeout_count} read timeouts so far; monitoring recovery")
            elif line.startswith("[DIAG] Position decoded") and not self.show_details.isChecked():
                pass  # Always preserved in raw console log and JSONL
            elif line.startswith("[DIAG] Position rejected") and not self.show_details.isChecked():
                pass
            else:
                self.append(line)
            if self.mode == "scan":
                match = SENDER_LINE.match(line)
                if match:
                    ant, dtype, tx, _source = match.groups()
                    ant = int(ant)
                    dtype = int(dtype) if dtype != "?" else None
                    tx = int(tx) if tx != "?" else None
                    self.add_sender({"ant_device_number": ant, "device_type": dtype,
                                     "transmission_type": tx,
                                     "garmin_menu_id": ((tx >> 4) << 16) | ant if tx is not None else None})

    def process_finished(self, exit_code, exit_status):
        self.read_output()
        if self._output_pending:
            self.append(self._output_pending)
            self._output_pending = ""
        if self._usb_timeout_count:
            self.append(f"[USB] Total read timeouts in this run: {self._usb_timeout_count}")
        if self._raw_output_file is not None:
            self._raw_output_file.write(f"=== {self.mode.upper()} STOPPED (exit {exit_code}) ===\n")
            self._raw_output_file.close()
            self._raw_output_file = None
        self.append(f"=== {self.mode.upper()} STOPPED (exit {exit_code}) ===")
        self.status.setText("Ready — no ANT connection")
        if self.mode == "scan":
            self.show_scan_results()
        if self._stop_requested and exit_status == QProcess.ExitStatus.NormalExit:
            self.append("[PROCESS] Stopped cleanly")
        try:
            STOP_REQUEST.unlink(missing_ok=True)
        except OSError:
            pass
        self.proc.deleteLater()
        self.proc = None
        self._stop_requested = False
        self.refresh_buttons()

    def add_sender(self, d):
        ant = d.get("ant_device_number")
        if ant is None:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole).get("ant_device_number") == ant:
                # Keep a complete identity if a later observation is incomplete.
                existing = item.data(Qt.ItemDataRole.UserRole)
                d = {k: (v if v is not None else existing.get(k)) for k, v in d.items()}
                break
        else:
            row = self.table.rowCount()
            self.table.insertRow(row)
        values = [d.get("garmin_menu_id"), d.get("ant_device_number"),
                  d.get("device_type"), d.get("transmission_type")]
        for col, value in enumerate(values):
            cell = QTableWidgetItem("?" if value is None else str(value))
            cell.setData(Qt.ItemDataRole.UserRole, d)
            self.table.setItem(row, col, cell)
        self.status.setText(f"Scanning — {self.table.rowCount()} sender(s) found" if self.mode == "scan" else self.status.text())

    def show_scan_results(self):
        try:
            rows = json.loads(SCAN_RESULT.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            if self.table.rowCount():
                self.append(f"[SCAN] {self.table.rowCount()} sender(s) retained from live output")
            else:
                self.append("[SCAN] No result file or live senders; see diagnostics above.")
            return
        for d in rows:
            self.add_sender(d)
        self.append(f"[SCAN] Found {self.table.rowCount()} unique ANT senders.")

    def pair_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Choose sender", "Select a Garmin sender in the list first.")
            return
        d = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if d.get("device_type") != 41 or d.get("transmission_type") is None:
            QMessageBox.warning(self, "Incomplete identity", "Need complete ANT identity to pair safely.")
            return
        menu = d["garmin_menu_id"]
        answer = QMessageBox.question(self, "Confirm Garmin ID",
            f"Does your Garmin show Broadcast ID {menu} in its menu?\n\n"
            "Only pair if the number matches. This calculation is still being validated.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        record = {"ant_device_number": d["ant_device_number"],
                  "transmission_type": d["transmission_type"],
                  "garmin_menu_id": menu}
        tmp = PAIRING.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            tmp.replace(PAIRING)
        except OSError as e:
            QMessageBox.critical(self, "Cannot save pairing", str(e))
            return
        self.append(f"[PAIR] Saved Garmin {menu} / ANT {d['ant_device_number']} / TX {d['transmission_type']}")
        self.load_pairing()
        self.refresh_buttons()

    def stop_process(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning and not self._stop_requested:
            self._stop_requested = True
            self.append("[PROCESS] Requesting graceful ANT shutdown...")
            self.stop_btn.setEnabled(False)
            try:
                STOP_REQUEST.write_text("stop\n", encoding="ascii")
            except OSError as exc:
                self.append(f"[PROCESS] Stop request failed: {exc}")
                self.proc.terminate()
            QTimer.singleShot(10000, self.kill_if_running)

    def kill_if_running(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning and self._stop_requested:
            self.append("[PROCESS] ANT shutdown timed out; force stopping bridge")
            self.proc.kill()

    def closeEvent(self, event):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            try:
                STOP_REQUEST.write_text("stop\n", encoding="ascii")
            except OSError:
                pass
            if not self.proc.waitForFinished(3000):
                self.proc.kill()
                self.proc.waitForFinished(1500)
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow(); w.show()
    sys.exit(app.exec())
