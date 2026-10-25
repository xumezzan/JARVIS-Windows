"""Restrained semantic themes shared by the shell and protected task dialogs."""

from functools import cache
from pathlib import Path
from string import Template

from PySide6.QtGui import QFontDatabase

# Semantic surface/foreground pairs follow the shadcn convention, expressed in native QSS.
PALETTES: dict[str, dict[str, str]] = {
    "Midnight": {
        "canvas": "#101216",
        "surface": "#15181d",
        "raised": "#20242b",
        "foreground": "#edf2f8",
        "muted": "#9cabbc",
        "border": "#2c3139",
        "primary": "#a4c7f4",
        "primary_foreground": "#0a182a",
        "focus": "#a4cfff",
    },
    "Graphite": {
        "canvas": "#111214",
        "surface": "#1a1c1f",
        "raised": "#24272c",
        "foreground": "#f0f1f3",
        "muted": "#adb1ba",
        "border": "#373b43",
        "primary": "#b5ceee",
        "primary_foreground": "#131d2c",
        "focus": "#d4e5ff",
    },
}

_QSS = Template("""
QWidget { background: $canvas; color: $foreground;
    font-family: "Inter", "Helvetica Neue", "Segoe UI"; font-size: 14px; }
QMainWindow, QScrollArea, QWidget#dashboard, QWidget#page { background: $canvas; }
QWidget#column, QLabel { background: transparent; }
QLabel { border: none; }
QLabel#wordmark { font-size: 22px; font-weight: 600; padding-left: 10px; }
QLabel#pageTitle { color: $muted; font-size: 13px; }
QLabel#heroTitle { font-size: 30px; font-weight: 500; }
QLabel#title { font-size: 26px; font-weight: 600; }
QLabel#cardTitle { font-size: 17px; font-weight: 500; }
QLabel#muted, QLabel#state { color: $muted; font-size: 13px; }
QLabel#sectionHint, QLabel#inputHint { color: $muted; font-size: 12px; }
QLabel#state { min-height: 22px; }
QLabel#state[status="error"] { color: #ffb9c4; }
QLabel#state[status="cancelled"] { color: #edd196; }
QLabel#state[status="success"] { color: #81dcc5; }
QFrame#sidebar { background: $surface; border: none; border-right: 1px solid $border; }
QFrame#settingRow { background: transparent; border: none; border-bottom: 1px solid $border; }
QFrame#commandBar { background: $surface; border: 1px solid $border; border-radius: 14px; }
QFrame#commandBar[active="true"] { border-color: $focus; }
QPushButton { background: $raised; border: 1px solid $border; border-radius: 8px;
    padding: 10px 16px; font-weight: 500; }
QPushButton:hover { background: $border; border-color: $muted; }
QPushButton:focus { border: 2px solid $focus; }
QPushButton:pressed { background: $surface; }
QPushButton#nav { background: transparent; border: 1px solid transparent; color: $muted;
    text-align: left; padding: 10px 12px; font-size: 13px; font-weight: 400; }
QPushButton#nav:hover { background: $raised; color: $foreground; }
QPushButton#nav:checked { background: $raised; color: $foreground; font-weight: 500; }
QPushButton#nav:focus { border-color: $focus; }
QPushButton#primary { background: $primary; color: $primary_foreground; border-color: $primary; }
QPushButton#primary:hover { background: $focus; }
QPushButton#quiet, QPushButton#disclosure { background: transparent;
    border: 1px solid transparent; }
QPushButton#quiet { color: $muted; }
QPushButton#disclosure { text-align: left; padding-left: 0; font-size: 16px; }
QPushButton#quiet:hover, QPushButton#disclosure:hover { color: $primary; }
QPushButton#quiet:focus, QPushButton#disclosure:focus { border-color: $focus; }
QPushButton#disclosure:checked { color: $primary; }
QPushButton#toolEntry { text-align: left; padding: 16px; }
QPushButton#stop { color: #ffb9c4; border-color: #a66177; }
QPushButton#motion:checked { color: $primary; }
QPushButton:disabled { color: #8b929e; background: $surface; border-color: $border; }
QPlainTextEdit, QLineEdit, QListWidget, QComboBox { background: $surface;
    border: 1px solid $border; border-radius: 8px; padding: 10px;
    selection-background-color: #38577c; selection-color: #ffffff; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus { border-color: $focus; }
QPlainTextEdit#commandInput { background: transparent; border: none; padding: 0; font-size: 15px; }
QPlainTextEdit#transcript { color: $muted; font-size: 13px; }
QComboBox { padding: 9px 12px; font-size: 13px; }
QComboBox QAbstractItemView { background: $raised; color: $foreground;
    selection-background-color: $border; }
QComboBox#themePicker { min-width: 116px; }
QCheckBox { spacing: 8px; padding: 6px 0; }
QCheckBox::indicator, QListWidget::indicator { width: 16px; height: 16px;
    border: 1px solid $muted; border-radius: 3px; background: $canvas; }
QCheckBox::indicator:checked, QListWidget::indicator:checked { background: $primary;
    border: 2px solid $foreground; }
QCheckBox:focus { outline: none; border: 1px solid $focus; }
QGroupBox { border: 1px solid $border; border-radius: 10px; margin-top: 20px;
    padding: 20px 16px 16px; font-weight: 500; }
QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 6px; }
QTabWidget::pane { border: none; border-top: 1px solid $border; }
QTabBar::tab { background: transparent; color: $muted; padding: 12px 18px;
    border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $foreground; border-bottom-color: $primary; }
QTabBar::tab:hover { background: $raised; }
QListWidget#activity { background: transparent; border: none; padding: 0; outline: none; }
QListWidget::item { padding: 16px 4px; border-bottom: 1px solid $border; }
QListWidget::item:selected { background: $raised; }
QProgressBar { background: $border; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px; }
QProgressBar::chunk { background: $primary; border-radius: 2px; }
QFrame#monthCalendar { background: transparent; }
QLabel#monthTitle { font-weight: 500; }
QLabel#calendarWeekday { color: $muted; font-size: 12px; }
QLabel#calendarDay { color: $muted; font-size: 13px; }
QLabel#calendarToday { color: $primary_foreground; background: $primary;
    border-radius: 6px; font-size: 13px; font-weight: 500; }
QScrollBar:vertical { background: $canvas; width: 8px; margin: 0; }
QScrollBar:horizontal { background: $canvas; height: 8px; margin: 0; }
QScrollBar::handle { background: $border; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { color: $foreground; background: $raised; border: 1px solid $border; padding: 6px; }
""")


def build_stylesheet(theme: str = "Midnight") -> str:
    """Render the same component styles using an explicit named palette."""
    return _QSS.substitute(PALETTES[theme])


STYLESHEET = build_stylesheet()


@cache
def load_fonts() -> None:
    """Register the bundled OFL font once, without changing system font settings."""
    QFontDatabase.addApplicationFont(str(Path(__file__).parent / "assets" / "Inter.ttf"))
