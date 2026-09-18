"""Midnight dashboard palette with shared, readable permission-dialog controls."""

from functools import cache
from pathlib import Path
from string import Template

from PySide6.QtGui import QFontDatabase

# Semantic surface/foreground pairs follow the shadcn convention, expressed in native QSS.
PALETTES: dict[str, dict[str, str]] = {
    "Midnight": {
        "canvas": "#090d12",
        "surface": "#111820",
        "raised": "#1a2430",
        "foreground": "#edf2f8",
        "muted": "#9cabbc",
        "border": "#26313f",
        "primary": "#79b8ff",
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
    font-family: "Inter", "Helvetica Neue", "Segoe UI";
    font-size: 14px; }
QMainWindow, QScrollArea { background: $canvas; }
QWidget#dashboard { background: $canvas; }
QWidget#column, QFrame#monthCalendar { background: transparent; }
QLabel { background: transparent; border: none; }
QLabel#brandMark { color: #83bdff; font-size: 27px; }
QLabel#wordmark { font-size: 20px; padding-left: 6px; font-weight: 400; }
QLabel#heroTitle { font-size: 54px; font-weight: 300; }
QLabel#cardTitle { font-size: 18px; font-weight: 500; }
QLabel#title { font-size: 25px; font-weight: 600; }
QLabel#muted { color: $muted; font-size: 13px; line-height: 1.5; }
QLabel#eyebrow { color: $muted; font-size: 12px; }
QLabel#state { color: $muted; font-size: 13px; min-height: 24px; }
QLabel#state[status="error"] { color: #ffb9c4; }
QLabel#state[status="cancelled"] { color: #edd196; }
QLabel#state[status="success"] { color: #81dcc5; }
QLabel#badge { color: #83bdff; background: #192b40; padding: 7px 12px; border-radius: 6px; }
QFrame#sidebar { background: $surface; border: 1px solid $border; border-radius: 16px; }
QFrame#card { background: $surface; border: 1px solid $border; border-radius: 16px; }
QFrame#card:focus { border: 1px solid $focus; }
QFrame#suggestion { background: $surface; border: 1px solid $border; border-radius: 16px; }
QLabel#monthTitle { color: $foreground; font-size: 14px; font-weight: 500; }
QLabel#calendarWeekday { color: $muted; font-size: 11px; }
QLabel#calendarDay { color: $muted; font-size: 13px; }
QLabel#calendarToday { color: $primary_foreground; background: $primary;
    border-radius: 8px; font-size: 13px; font-weight: 600; }
QPushButton#toolEntry { background: $raised; border: 1px solid $border;
    text-align: left; padding: 14px; border-radius: 10px; font-size: 14px; }
QPushButton#toolEntry:hover { border-color: $primary; }
QLabel#sectionHint { color: $muted; font-size: 12px; }
QLabel#appSymbol { font-size: 27px; font-weight: 600; }
QLabel#appCaption { color: #a1adbc; font-size: 10px; }
QLabel#avatar { background: #253342; border: 1px solid #35485e; border-radius: 20px;
    color: #a2ccff; font-size: 19px; }
QLabel#greeting { color: #c0cad7; font-size: 12px; }
QLabel#emptyTitle { color: #c8d2df; font-size: 14px; }
QLabel#quote { color: #adb9c9; background: #18212a; border: 1px solid #25303b;
    border-radius: 12px; padding: 12px; font-size: 12px; }
QLabel#spark { font-size: 27px; color: #73b8ff; }
QLabel#inputHint { color: #96a5b8; font-size: 11px; }
QFrame#commandBar { background: $surface; border: 1px solid $border; border-radius: 18px; }
QFrame#commandBar[active="true"] { border: 1px solid $primary; }
QGroupBox { border: 1px solid #334457; border-radius: 10px; margin-top: 18px;
    padding: 20px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
QPlainTextEdit, QLineEdit, QListWidget, QComboBox { background: $raised;
    border: 1px solid $border; border-radius: 8px; padding: 10px;
    selection-background-color: #315f96; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid $focus; }
QPlainTextEdit#commandInput { background: transparent; border: none; padding: 8px 0;
    font-size: 14px; }
QPlainTextEdit#transcript { background: $canvas; border: 1px solid $border;
    font-size: 12px; padding: 8px; }
/* Every checkbox draws its own box: consent the owner cannot see is consent they cannot give. */
QCheckBox::indicator, QWidget#memoryPanel QListWidget::indicator {
    width: 16px; height: 16px; border: 1px solid $muted; border-radius: 3px;
    background: $canvas; }
QCheckBox::indicator:hover { border: 1px solid $primary; }
QCheckBox::indicator:checked,
QWidget#memoryPanel QListWidget::indicator:checked { background: $primary;
    border: 2px solid $foreground; }
QCheckBox:focus { border: 1px solid $focus; }
QListWidget#activity { background: transparent; border: none; padding: 0; outline: none; }
QListWidget::item { padding: 16px 4px; border-bottom: 1px solid #232e3a; }
QListWidget::item:selected { background: #1b2b3f; border-radius: 8px; }
QPushButton { background: $raised; border: 1px solid $border; border-radius: 10px;
    padding: 10px 14px; font-weight: 500; }
QPushButton:hover { background: #2b3d53; border-color: #527399; }
QPushButton:focus { border: 2px solid $focus; }
QPushButton:pressed { background: #1a2b40; }
QPushButton#nav { background: transparent; border: 1px solid transparent; color: $muted;
    text-align: left; padding: 10px 12px; font-size: 13px; font-weight: 400; }
QPushButton#nav:hover { background: #141f2a; color: #d5e7fe; }
QPushButton#nav:checked { background: $raised; color: $primary; border-color: $border; }
QPushButton#nav:focus { border-color: #83bdff; }
QPushButton#round { background: $surface; border: 1px solid $border;
    border-radius: 24px; padding: 0; }
QPushButton#round:hover { background: #24364b; }
QPushButton#microphone { background: #121f31; border: 2px solid $primary;
    border-radius: 36px; padding: 0; }
QPushButton#send { background: #283b54; border: 1px solid #3e5978;
    border-radius: 21px; padding: 0; }
QPushButton#send:hover { background: #34639d; }
QPushButton#primary { background: $primary; color: $primary_foreground;
    border: 1px solid $primary; }
QPushButton#primary:hover { background: $focus; }
QPushButton#stop { color: #ffb9c4; border: 1px solid #a66177; }
QPushButton:disabled { color: #77899e; background: #17222f; border: 1px solid #2a384a; }
QPushButton#send:disabled { background: #17222f; border-color: #2a384a; }
QComboBox#themePicker { min-width: 92px; }
QPushButton#link { background: transparent; border: none; color: $primary;
    font-size: 12px; padding: 2px 4px; text-decoration: underline; }
QPushButton#link:hover { color: $focus; }
QPushButton#motion { font-size: 12px; padding: 7px 12px; }
QPushButton#motion:checked { color: $primary; }
QComboBox { padding: 7px 12px; color: #a4b4c9; font-size: 11px; }
QComboBox QAbstractItemView { background: #1b2735; color: #e8eef5; }
QProgressBar { background: #202b37; border: none; border-radius: 3px;
    min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: $primary; border-radius: 3px; }
QScrollBar:vertical { background: #0d141c; width: 8px; margin: 0; }
QScrollBar:horizontal { background: #0d141c; height: 8px; margin: 0; }
QScrollBar::handle { background: #35465a; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { color: #dce9fa; background: #1b2c40; border: 1px solid #476387; padding: 6px; }
""")


ICON = Path(__file__).parent / "assets" / "jarvis.ico"


def build_stylesheet(theme: str = "Midnight") -> str:
    """Render the same component styles using an explicit named palette."""
    return _QSS.substitute(PALETTES[theme])


STYLESHEET = build_stylesheet()


@cache
def load_fonts() -> None:
    """Register the bundled OFL font once, without changing system font settings."""
    QFontDatabase.addApplicationFont(str(Path(__file__).parent / "assets" / "Inter.ttf"))
