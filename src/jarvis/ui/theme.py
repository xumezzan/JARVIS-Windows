"""Midnight dashboard palette with shared, readable permission-dialog controls."""

STYLESHEET = """
QWidget { background: #0b1015; color: #ecf0f6; font-family: "Helvetica Neue", "Segoe UI";
    font-size: 14px; }
QMainWindow, QScrollArea { background: #0b1015; }
QWidget#dashboard { background: qradialgradient(cx:0.49, cy:0.43, radius:0.7,
    fx:0.49, fy:0.43, stop:0 #111b29, stop:0.43 #0c1219, stop:1 #090e12); }
QLabel { background: transparent; border: none; }
QLabel#brandMark { color: #83bdff; font-size: 27px; }
QLabel#wordmark { font-size: 20px; padding-left: 6px; font-weight: 400; }
QLabel#heroTitle { font-size: 56px; font-weight: 200; }
QLabel#cardTitle { font-size: 19px; font-weight: 500; }
QLabel#title { font-size: 25px; font-weight: 600; }
QLabel#muted { color: #99a5b6; font-size: 13px; line-height: 1.5; }
QLabel#eyebrow { color: #8493a7; font-size: 10px; letter-spacing: 2px; }
QLabel#state { color: #9caec5; font-size: 11px; letter-spacing: 2px; min-height: 24px; }
QLabel#state[status="error"] { color: #ffb9c4; }
QLabel#state[status="cancelled"] { color: #edd196; }
QLabel#state[status="success"] { color: #81dcc5; }
QLabel#badge { color: #83bdff; background: #192b40; padding: 7px 12px; border-radius: 6px; }
QFrame#sidebar { background: transparent; }
QFrame#card { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #11181f, stop:1 #0e151b); border: 1px solid #202a33; border-radius: 18px; }
QFrame#card:focus { border: 1px solid #608dc8; }
QFrame#suggestion { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #1a232d, stop:1 #121920); border: 1px solid #27313b; border-radius: 18px; }
QFrame#appTile { background: #1a222b; border: 1px solid #242e38; border-radius: 14px; }
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
QFrame#commandBar { background: #121a23; border: 1px solid #303d4a; border-radius: 26px; }
QGroupBox { border: 1px solid #334457; border-radius: 10px; margin-top: 18px;
    padding: 20px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
QPlainTextEdit, QLineEdit, QListWidget, QComboBox { background: #131d28;
    border: 1px solid #354459; border-radius: 8px; padding: 10px;
    selection-background-color: #315f96; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid #79b5ff; }
QPlainTextEdit#commandInput { background: transparent; border: none; padding: 8px 0;
    font-size: 13px; }
QPlainTextEdit#transcript { background: #10171f; border: 1px solid #263442;
    font-size: 12px; padding: 8px; }
QListWidget#activity { background: transparent; border: none; padding: 0; outline: none; }
QListWidget::item { padding: 16px 4px; border-bottom: 1px solid #232e3a; }
QListWidget::item:selected { background: #1b2b3f; border-radius: 8px; }
QPushButton { background: #202c3a; border: 1px solid #364559; border-radius: 10px;
    padding: 10px 14px; font-weight: 500; }
QPushButton:hover { background: #2b3d53; border-color: #527399; }
QPushButton:focus { border: 2px solid #83bdff; }
QPushButton:pressed { background: #1a2b40; }
QPushButton#nav { background: transparent; border: 1px solid transparent; color: #9aa9bc;
    text-align: left; padding: 10px 7px; font-size: 13px; font-weight: 400; }
QPushButton#nav:hover { background: #141f2a; color: #d5e7fe; }
QPushButton#nav:checked { background: #17212b; color: #8fc7ff; border-color: #1c2a37; }
QPushButton#nav:focus { border-color: #83bdff; }
QPushButton#round { background: #131c26; border: 1px solid #273342;
    border-radius: 26px; padding: 0; }
QPushButton#round:hover { background: #24364b; }
QPushButton#microphone:disabled { background: #121f31; border: 2px solid #65a8ff;
    border-radius: 36px; padding: 0; }
QPushButton#send { background: #283b54; border: 1px solid #3e5978;
    border-radius: 21px; padding: 0; }
QPushButton#send:hover { background: #34639d; }
QPushButton#primary { background: #336bb9; color: #f5f8ff; border: 1px solid #5389d1; }
QPushButton#primary:hover { background: #427fce; }
QPushButton#stop { color: #ffb9c4; border: 1px solid #a66177; }
QPushButton:disabled { color: #77899e; background: #17222f; border: 1px solid #2a384a; }
QPushButton#send:disabled { background: #17222f; border-color: #2a384a; }
QComboBox { padding: 7px 12px; color: #a4b4c9; font-size: 11px; }
QComboBox QAbstractItemView { background: #1b2735; color: #e8eef5; }
QProgressBar { background: #202b37; border: none; border-radius: 3px;
    min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #619cff; border-radius: 3px; }
QScrollBar:vertical { background: #0d141c; width: 8px; margin: 0; }
QScrollBar:horizontal { background: #0d141c; height: 8px; margin: 0; }
QScrollBar::handle { background: #35465a; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { color: #dce9fa; background: #1b2c40; border: 1px solid #476387; padding: 6px; }
"""
