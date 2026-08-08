"""
Token Usage & Costs report dialog for Supervertaler Workbench.

Reads the JSONL usage ledger (modules.usage_log), totals it grouped by
project/client/model/etc. over a date range, and exports the detailed ledger
to CSV or Excel. Mirrors the Trados plugin's Usage & Costs report.
"""

import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from modules import usage_log


class UsageReportDialog(QDialog):
    def __init__(self, parent=None, budget: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle("Token Usage & Costs")
        self.resize(860, 540)
        self._records = []
        self._mtd = 0.0
        self._budget = float(budget or 0.0)

        top = QHBoxLayout()
        top.addWidget(QLabel("Range:"))
        self.cmb_range = QComboBox()
        self.cmb_range.addItems(["This month", "Last 3 months", "This year", "All time"])
        self.cmb_range.currentIndexChanged.connect(self.reload)
        top.addWidget(self.cmb_range)

        top.addSpacing(12)
        top.addWidget(QLabel("Group by:"))
        self.cmb_group = QComboBox()
        self.cmb_group.addItems(usage_log.DIMENSIONS)
        self.cmb_group.currentIndexChanged.connect(self.rebind)
        top.addWidget(self.cmb_group)

        top.addSpacing(12)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.reload)
        top.addWidget(btn_refresh)
        btn_csv = QPushButton("Export CSV…")
        btn_csv.clicked.connect(lambda: self.export(xlsx=False))
        top.addWidget(btn_csv)
        btn_xlsx = QPushButton("Export Excel…")
        btn_xlsx.clicked.connect(lambda: self.export(xlsx=True))
        top.addWidget(btn_xlsx)
        top.addStretch(1)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Group", "Calls", "Input", "Output", "Cost (USD)", "% actual"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)

        self.lbl_totals = QLabel("")
        self.lbl_totals.setStyleSheet("font-weight: bold; padding: 4px;")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addWidget(self.lbl_totals)

        self.reload()

    def _range(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        i = self.cmb_range.currentIndex()
        if i == 0:
            frm = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            to = now
        elif i == 1:
            frm = now - datetime.timedelta(days=90)
            to = now
        elif i == 2:
            frm = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            to = now
        else:
            frm = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
            to = datetime.datetime(2999, 1, 1, tzinfo=datetime.timezone.utc)
        return frm, to

    def reload(self):
        frm, to = self._range()
        try:
            self._records = usage_log.load(frm, to)
        except Exception:
            self._records = []
        try:
            self._mtd = usage_log.month_to_date_cost()
        except Exception:
            self._mtd = 0.0
        self.rebind()

    def rebind(self):
        dim = self.cmb_group.currentText() or "Project"
        rows = usage_log.group(self._records, dim)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cells = [
                row["group"],
                f"{row['calls']:,}",
                f"{row['input']:,}",
                f"{row['output']:,}",
                f"{row['cost_usd']:.4f}",
                f"{row['actual_pct']}%",
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

        t = usage_log.totals(self._records)
        if self._budget and self._budget > 0:
            pct = (self._mtd / self._budget * 100.0) if self._budget else 0.0
            month = f"     |     This month: ${self._mtd:.2f} of ${self._budget:.2f} budget ({pct:.0f}%)"
        else:
            month = f"     |     This month: ${self._mtd:.2f}"
        self.lbl_totals.setText(
            f"Range total: {t['calls']:,} calls · {t['input']:,} in / {t['output']:,} out · "
            f"${t['cost_usd']:.2f} · {t['actual_pct']}% from provider" + month)

    def export(self, xlsx: bool):
        try:
            default = "supervertaler-usage-" + datetime.date.today().strftime("%Y-%m-%d") + (".xlsx" if xlsx else ".csv")
            flt = "Excel workbook (*.xlsx)" if xlsx else "CSV file (*.csv)"
            path, _ = QFileDialog.getSaveFileName(self, "Export usage ledger", default, flt)
            if not path:
                return
            if xlsx:
                usage_log.export_xlsx(path, self._records)
            else:
                usage_log.export_csv(path, self._records)
            QMessageBox.information(
                self, "Export complete",
                f"Exported {len(self._records):,} record(s) to:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export", f"Export failed: {e}")
