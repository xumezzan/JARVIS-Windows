"""Desktop palette with native layouts, keyboard focus, and readable contrast."""

STYLESHEET = """
QWidget { background: #101720; color: #e8eef5; font-size: 14px; }
QMainWindow { background: #101720; }
QLabel { background: transparent; }
QLabel#brand { font-size: 30px; font-weight: 700; letter-spacing: 4px; }
QLabel#title { font-size: 25px; font-weight: 600; }
QLabel#muted { color: #a7b7c9; }
QLabel#badge { color: #70ded1; background: #193832; padding: 7px 12px; border-radius: 6px; }
QLabel#state { color: #9ff0e3; font-size: 17px; font-weight: 600; padding: 8px 0; }
QLabel#state[status="error"] { color: #ffb9b9; }
QLabel#state[status="cancelled"] { color: #edd196; }
QFrame#sidebar { background: #151f2b; border-radius: 12px; }
QGroupBox { border: 1px solid #334457; border-radius: 10px; margin-top: 18px;
    padding: 20px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
QPlainTextEdit, QLineEdit, QListWidget, QComboBox { background: #151f2b; border: 1px solid #41536a;
    border-radius: 7px; padding: 10px; selection-background-color: #246a66; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid #70ded1; }
QListWidget::item { padding: 8px 4px; border-bottom: 1px solid #293848; }
QPushButton { background: #243446; border: 1px solid #41536a; border-radius: 7px;
    padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #31475d; }
QPushButton:focus { border: 2px solid #9ff0e3; }
QPushButton#primary { background: #70ded1; color: #102c2a; border: 1px solid #70ded1; }
QPushButton#primary:hover { background: #a1eee3; }
QPushButton#stop { color: #ffb9b9; border: 1px solid #a66167; }
QPushButton:disabled { color: #8b9caf; background: #1b2735; border: 1px solid #344353; }
QPushButton#primary:disabled, QPushButton#stop:disabled {
    color: #8b9caf; background: #1b2735; border: 1px solid #344353; }
QComboBox QAbstractItemView { background: #1b2735; color: #e8eef5; }
QProgressBar { background: #243446; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #70ded1; border-radius: 3px; }
"""
