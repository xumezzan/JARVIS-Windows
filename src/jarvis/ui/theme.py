"""Midnight dashboard palette with shared, readable permission-dialog controls."""

from functools import cache
from pathlib import Path
from string import Template

from PySide6.QtGui import QFontDatabase

# Semantic surface/foreground pairs follow the shadcn convention, expressed in native QSS.
# Two palettes, one set of names: every rule below is written once and reads the tokens, so
# a colour is changed in one place rather than hunted through the sheet.
PALETTES: dict[str, dict[str, str]] = {
    "Midnight": {
        "canvas": "#070a10",
        "surface": "#0d131c",
        "raised": "#141c28",
        "hover": "#1b2534",
        "foreground": "#e9eff7",
        "muted": "#8496ab",
        "faint": "#63748a",
        "border": "#1b2634",
        "line": "#161f2b",
        "primary": "#5b9dff",
        "primary_foreground": "#06101f",
        "focus": "#8fc0ff",
        "glow": "#28415f",
        "success": "#61d6b4",
        "warning": "#e5c07b",
        "danger": "#ff9db0",
    },
    "Graphite": {
        "canvas": "#0d0e11",
        "surface": "#16181d",
        "raised": "#1f232a",
        "hover": "#282d36",
        "foreground": "#eef0f3",
        "muted": "#a3a9b4",
        "faint": "#7d838f",
        "border": "#2a2f38",
        "line": "#23272f",
        "primary": "#9dbde4",
        "primary_foreground": "#101821",
        "focus": "#cbdcf5",
        "glow": "#2a3442",
        "success": "#9ad9c4",
        "warning": "#ddc394",
        "danger": "#f0b3bf",
    },
}

_QSS = Template("""
/* ---- Foundation ------------------------------------------------------------------ */
QWidget { background: $canvas; color: $foreground;
    font-family: "Inter", "Helvetica Neue", "Segoe UI";
    font-size: 13px; }
QMainWindow, QScrollArea { background: $canvas; }
QWidget#dashboard { background: $canvas; }
QWidget#column, QFrame#monthCalendar, QWidget#zone { background: transparent; }
QLabel { background: transparent; border: none; }

/* ---- Typography ------------------------------------------------------------------ */
QLabel#brandMark { color: $primary; }
QLabel#wordmark { font-size: 17px; font-weight: 600; letter-spacing: 0.3px; }
QLabel#tagline { color: $faint; font-size: 11px; }
QLabel#greetingTitle { font-size: 30px; font-weight: 300; letter-spacing: 0.2px; }
QLabel#greetingLine { color: $muted; font-size: 14px; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QLabel#muted { color: $muted; font-size: 13px; }
QLabel#sectionHint { color: $muted; font-size: 12px; }
QLabel#state { font-size: 12px; font-weight: 600; min-height: 16px; }
QLabel#state[status="error"] { color: $danger; }
QLabel#state[status="cancelled"] { color: $warning; }
QLabel#state[status="success"] { color: $success; }
QLabel#inputHint { color: $faint; font-size: 11px; }
QLabel#avatar { background: $raised; border: 1px solid $border; border-radius: 17px;
    color: $primary; font-size: 15px; font-weight: 600; }

/* ---- Settings: title, rows, chips and the one primary action ---------------------- */
QLabel#screenTitle { font-size: 26px; font-weight: 600; letter-spacing: 0.2px; }
QLabel#identityTitle { font-size: 21px; font-weight: 600; }
QLabel#rowTitle { font-size: 13px; font-weight: 600; }
QLabel#rowCaption { color: $muted; font-size: 12px; }
QLabel#quote { color: $faint; font-size: 12px; font-style: italic; }
QFrame#integration { background: transparent; border: none; }
QLabel[role="chip"] { color: $faint; background: $raised; border: 1px solid $line;
    border-radius: 9px; padding: 4px 10px; font-size: 11px; font-weight: 500;
    min-height: 14px; max-height: 14px; }
QLabel[role="chip"][state="on"] { color: $success; border-color: $glow; }
QLabel[role="chip"][state="off"] { color: $faint; }
QLabel[role="chip"][state="pending"] { color: $primary; border-color: $glow; }
QPushButton#accent { background: $glow; color: $foreground; border: 1px solid $primary;
    border-radius: 10px; padding: 8px 16px; font-weight: 500; }
QPushButton#accent:hover { background: $primary; color: $primary_foreground; }
QPushButton#accent:focus { border: 2px solid $focus; }
QPushButton#accent:disabled { background: $surface; color: $faint; border-color: $line; }
QPushButton#gear { background: transparent; border: 1px solid transparent; border-radius: 9px;
    padding: 0; }
QPushButton#gear:hover { background: $raised; border-color: $border; }
QPushButton#gear:focus { border: 1px solid $focus; }
QPushButton#gear::menu-indicator { width: 0; height: 0; }
QPushButton#save { background: $primary; color: $primary_foreground; border: 2px solid $glow;
    border-radius: 12px; padding: 10px 26px; font-size: 13px; font-weight: 600; }
QPushButton#save:hover { background: $focus; border-color: $primary; }
QPushButton#save:focus { border: 2px solid $focus; }
QMenu { background: $raised; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 14px; border-radius: 7px; }
QMenu::item:selected { background: $hover; }

/* ---- Panels ---------------------------------------------------------------------- */
QFrame#sidebar, QFrame#card { background: $surface;
    border: 1px solid $border; border-radius: 18px; }
QFrame#card:focus { border: 1px solid $focus; }
QFrame#divider { background: $line; border: none; }
QFrame#inset { background: $raised; border: 1px solid $line; border-radius: 12px; }
QFrame#inset QLabel { background: transparent; }

/* ---- Navigation rail ------------------------------------------------------------- */
QPushButton#nav { background: transparent; border: 1px solid transparent; color: $muted;
    text-align: left; padding: 10px 12px; font-size: 13px; font-weight: 500;
    border-radius: 10px; }
QPushButton#nav:hover { background: $raised; color: $foreground; }
QPushButton#nav:checked { background: $glow; color: $foreground; border-color: $primary; }
QPushButton#nav:focus { border: 1px solid $focus; }

/* ---- Status pill ----------------------------------------------------------------- */
QFrame#statusPill { background: $raised; border: 1px solid $border; border-radius: 14px; }
QLabel#pillCaption { color: $faint; font-size: 11px; }
QLabel#dot { border-radius: 4px; background: $muted; min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px; }
QLabel#dot[tone="live"] { background: $success; }
QLabel#dot[tone="busy"] { background: $primary; }
QLabel#dot[tone="warn"] { background: $warning; }
QLabel#dot[tone="off"] { background: $faint; }
QLabel#dot[tone="error"] { background: $danger; }

/* ---- Command bar ----------------------------------------------------------------- */
QFrame#commandBar { background: $surface; border: 1px solid $border; border-radius: 26px; }
QFrame#commandBar[active="true"] { border: 1px solid $primary; background: $raised; }
QPlainTextEdit#commandInput { background: transparent; border: none; padding: 6px 0;
    font-size: 14px; }
QPushButton#microphone { background: $raised; border: 1px solid $border;
    border-radius: 21px; padding: 0; }
QPushButton#microphone:hover { background: $hover; border-color: $primary; }
QPushButton#microphone:focus { border: 2px solid $focus; }
QPushButton#microphone:checked { background: $primary; border-color: $primary; }
QPushButton#send { background: $primary; border: 1px solid $primary;
    border-radius: 21px; padding: 0; }
QPushButton#send:hover { background: $focus; border-color: $focus; }
QPushButton#send:focus { border: 2px solid $focus; }
QPushButton#send:pressed { background: $primary_foreground; border-color: $primary; }

/* ---- Tiles: quick actions, applications, examples --------------------------------- */
QPushButton#chip { background: $surface; border: 1px solid $border; border-radius: 12px;
    padding: 9px 14px; color: $foreground; font-size: 12px; font-weight: 500;
    text-align: center; }
QPushButton#chip:hover { background: $raised; border-color: $primary; }
QPushButton#chip:focus { border: 1px solid $focus; }
QPushButton#chip:pressed { background: $hover; }
QPushButton#tile { background: $raised; border: 1px solid $line; border-radius: 14px;
    text-align: left; padding: 0; }
QPushButton#tile:hover { background: $hover; border-color: $primary; }
QPushButton#tile:focus { border: 1px solid $focus; }
QPushButton#tile:pressed { background: $raised; }
QPushButton#tile QLabel { background: transparent; }
QLabel#tileTitle { font-size: 13px; font-weight: 500; }
QLabel#tileCaption { color: $faint; font-size: 11px; }
QPushButton#appTile { background: $raised; border: 1px solid $line; border-radius: 12px;
    padding: 0; }
QPushButton#appTile:hover { background: $hover; border-color: $primary; }
QPushButton#appTile:focus { border: 1px solid $focus; }
QPushButton#appTile QLabel { background: transparent; }
QPushButton#appTile[connected="false"] QLabel#appName { color: $faint; }
QLabel#appName { font-size: 10px; color: $muted; }
QLabel#counter { color: $muted; font-size: 12px; }

/* ---- Day: month grid and the events under it -------------------------------------- */
QLabel#calendarWeekday { color: $faint; font-size: 11px; }
QLabel#calendarDay { color: $muted; font-size: 12px; }
QLabel#calendarToday { color: $primary_foreground; background: $primary;
    border-radius: 8px; font-size: 12px; font-weight: 600; }
QLabel#eventTime { color: $muted; font-size: 12px; }
QLabel#eventTitle { font-size: 13px; font-weight: 500; }
QLabel#eventPlace { color: $faint; font-size: 11px; }

/* ---- Journal, summary, agent ------------------------------------------------------ */
QListWidget#activity { background: transparent; border: none; padding: 0; outline: none; }
QListWidget#activity::item { padding: 10px 6px; border-bottom: 1px solid $line;
    border-radius: 10px; }
QListWidget#activity::item:selected { background: $raised; }
QListWidget::item { padding: 10px 4px; border-bottom: 1px solid $line; }
QListWidget::item:selected { background: $raised; border-radius: 8px; }
QLabel#summaryText { color: $muted; font-size: 12px; }
QFrame#agentCard { background: $surface; border: 1px solid $border; border-radius: 18px; }

/* ---- Inputs ----------------------------------------------------------------------- */
QGroupBox { border: 1px solid $border; border-radius: 12px; margin-top: 18px;
    padding: 20px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
QPlainTextEdit, QLineEdit, QListWidget, QComboBox { background: $raised;
    border: 1px solid $border; border-radius: 10px; padding: 9px;
    selection-background-color: $glow; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid $focus; }
QPlainTextEdit#transcript { background: $canvas; border: 1px solid $line;
    font-size: 12px; padding: 8px; }
/* A checkbox sits on whatever panel it is in, not on its own darker rectangle. */
QCheckBox, QRadioButton { background: transparent; }
/* Every checkbox draws its own box: consent the owner cannot see is consent they cannot give. */
QCheckBox::indicator, QWidget#memoryPanel QListWidget::indicator {
    width: 16px; height: 16px; border: 1px solid $muted; border-radius: 4px;
    background: $canvas; }
QCheckBox::indicator:hover { border: 1px solid $primary; }
QCheckBox::indicator:checked,
QWidget#memoryPanel QListWidget::indicator:checked { background: $primary;
    border: 2px solid $foreground; }
QCheckBox:focus { border: 1px solid $focus; }

/* ---- Buttons ---------------------------------------------------------------------- */
QPushButton { background: $raised; border: 1px solid $border; border-radius: 10px;
    padding: 9px 14px; font-weight: 500; }
QPushButton:hover { background: $hover; border-color: $primary; }
QPushButton:focus { border: 2px solid $focus; }
QPushButton:pressed { background: $surface; }
QPushButton#round { background: $raised; border: 1px solid $border;
    border-radius: 18px; padding: 0; }
QPushButton#round:hover { background: $hover; border-color: $primary; }
QPushButton#primary { background: $primary; color: $primary_foreground;
    border: 1px solid $primary; }
QPushButton#primary:hover { background: $focus; border-color: $focus; }
QPushButton#stop { color: $danger; border: 1px solid $danger; }
QPushButton:disabled { color: $faint; background: $surface; border: 1px solid $line; }
QPushButton#send:disabled { background: $raised; border-color: $border; }
QPushButton#link { background: transparent; border: none; color: $primary;
    font-size: 12px; padding: 2px 4px; }
QPushButton#link:hover { color: $focus; text-decoration: underline; }
QPushButton#link:focus { border: 1px solid $focus; border-radius: 6px; }
QPushButton#motion { font-size: 12px; padding: 7px 12px; }
QPushButton#motion:checked { color: $primary; }
QComboBox { padding: 7px 12px; color: $foreground; font-size: 12px; }
QComboBox#themePicker { min-width: 92px; }
QComboBox QAbstractItemView { background: $raised; color: $foreground;
    selection-background-color: $glow; }

/* ---- Chrome ----------------------------------------------------------------------- */
QProgressBar { background: $raised; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px; }
QProgressBar::chunk { background: $primary; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
QScrollBar::handle { background: $border; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: $hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { color: $foreground; background: $raised; border: 1px solid $border;
    border-radius: 8px; padding: 6px; }
QTabWidget::pane { border: 1px solid $border; border-radius: 12px; }
QTabBar::tab { background: transparent; color: $muted; padding: 8px 14px;
    border: 1px solid transparent; border-radius: 9px; margin-right: 4px; }
QTabBar::tab:selected { background: $raised; color: $foreground; border-color: $border; }
QTabBar::tab:hover { color: $foreground; }
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
