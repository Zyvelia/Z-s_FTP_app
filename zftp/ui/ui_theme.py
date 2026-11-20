"""Centralized charcoal/violet desktop theme (no network or filesystem effects)."""
APP_STYLESHEET = r'''
QMainWindow, QWidget { background: #10121a; color: #e8e8f1; font: 12px "Segoe UI"; }
QWidget#appHeader { background: #191b29; border-bottom: 1px solid #36354b; }
QLabel#appTitle { font-size: 20px; font-weight: 750; color: #f4f0ff; }
QLabel#subTitle, QLabel#mutedLabel { color: #a4a6ba; }
QLabel#statusBadge { background: #26283a; border: 1px solid #41415e; color: #ddd6fe; border-radius: 9px; padding: 5px 10px; font-weight: 600; }
QLabel#statusBadge[status="on"] { color: #a7f3d0; border-color: #276957; background: #14382f; }
QLabel#statusBadge[status="off"] { color: #c7cad8; border-color: #444555; }
QLabel#sectionHeading { font-weight: 700; font-size: 13px; color: #d9cafc; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QListWidget, QTableWidget { background: #191c29; border: 1px solid #363a50; border-radius: 6px; padding: 5px; selection-background-color: #55418d; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTableWidget:focus { border: 1px solid #a78bfa; }
QPushButton { background: #302b48; border: 1px solid #4b416e; border-radius: 6px; padding: 7px 11px; color: #f0eafb; font-weight: 550; }
QPushButton:hover:enabled { background: #483b6d; border-color: #8b72bf; }
QPushButton:pressed:enabled { background: #3b315c; }
QPushButton:disabled { background: #242532; border-color: #343544; color: #797b90; }
QPushButton#primaryAction { background: #6c46b3; border-color: #9b75e9; font-weight: 700; }
QPushButton#primaryAction:hover:enabled { background: #8156ce; }
QHeaderView::section { background: #25283a; border: none; border-bottom: 1px solid #424059; padding: 5px 8px; font-weight: 600; color: #c9bedf; }
QTableWidget { gridline-color: #25293a; alternate-background-color: #202332; }
QTableWidget::item { padding: 2px 4px; }
QTableWidget::item:selected { background: #57418c; }
QTabWidget::pane { border: 1px solid #34374a; border-radius: 5px; background: #121420; }
QTabBar::tab { background: #252637; color: #b5b6c9; border: 1px solid #373a52; border-bottom: none; padding: 8px 15px; margin-right: 2px; }
QTabBar::tab:selected { background: #58418b; color: #fff; }
QTabBar::tab:hover { background: #37314e; }
QSplitter::handle { background: #343047; }
QProgressBar { border: 1px solid #46435b; border-radius: 5px; text-align: center; height: 12px; background: #262738; }
QProgressBar::chunk { background: #9b73e0; border-radius: 4px; }
QScrollBar:vertical { background: #191c29; width: 11px; }
QScrollBar::handle:vertical { background: #51486b; border-radius: 5px; min-height: 22px; }
QGroupBox { border: 1px solid #363a50; border-radius: 7px; margin-top: 12px; padding: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #d7c9f3; }
QStatusBar { color: #b0b2c3; background: #171925; border-top: 1px solid #31354a; }
QToolTip { background: #29253b; color: white; border: 1px solid #6b558f; }
'''


def status_label(count: int, active_transfers: int = 0) -> str:
    """Human-readable connection count for the toolbar/status bar."""
    if count < 0 or active_transfers < 0:
        raise ValueError('counts cannot be negative')
    return f'{count} connected' + (f' · {active_transfers} transferring' if active_transfers else '')

# Compact, consistent action hierarchy for v2.0.1.
APP_STYLESHEET += r'''
QWidget#connectionPanel { background: #181a28; border: 1px solid #303248; border-radius: 8px; }
QWidget#advancedPanel { background: #191c2a; border: 1px solid #373851; border-radius: 6px; }
QPushButton { min-height: 20px; padding: 5px 9px; }
QToolButton { min-height: 24px; padding: 4px 9px; border: 1px solid #4b416e; border-radius: 6px; background: #302b48; color: #f0eafb; }
QToolButton:hover { background: #483b6d; }
QMenu { background: #222437; color: #f1ecfa; border: 1px solid #4a4264; padding: 4px; }
QMenu::item { padding: 7px 22px; border-radius: 4px; }
QMenu::item:selected { background: #57418c; }
QMenu::item:disabled { color: #777a91; }
QTabBar::tab { padding: 6px 11px; }
QLabel#appTitle { font-size: 18px; }
QHeaderView::section { padding: 4px 7px; }
'''

# v2.0.7: restrained violet accents, rounded surfaces, compact controls.
APP_STYLESHEET += r'''
QMainWindow, QWidget { background: #10121b; color: #e5e8f6; font: 12px "Segoe UI"; }
QWidget#appHeader { background: #191c2c; border: 1px solid #30354d; border-radius: 12px; }
QWidget#connectionPanel { background: #191d2e; border: 1px solid #343852; border-radius: 10px; }
QWidget#advancedPanel { background: #171b2b; border: 1px solid #303852; border-radius: 10px; }
QLabel#appTitle { font-size: 19px; color: #f3efff; }
QLabel#sectionHeading { color: #c8b5fc; font-size: 13px; }
QLabel#emptyState { color: #8f9bb6; font-size: 14px; padding: 22px; }
QPushButton, QToolButton { border-radius: 8px; background: #292b42; border: 1px solid #454765; padding: 6px 12px; }
QPushButton:hover:enabled, QToolButton:hover:enabled { background: #383856; border-color: #8a73ce; }
QPushButton#primaryAction { background: #7052bf; border-color: #9c83ed; }
QPushButton#primaryAction:hover:enabled { background: #8064d2; }
QLineEdit, QComboBox, QSpinBox { background: #171a2a; border: 1px solid #373d58; border-radius: 8px; padding: 6px; }
QTableWidget { background: #171b2a; alternate-background-color: #1d2234; border-radius: 9px; border: 1px solid #363c56; }
QHeaderView::section { background: #252b40; color: #c4cae2; padding: 7px; }
QTabWidget::pane { border: 1px solid #343b56; border-radius: 9px; }
QTabBar::tab { padding: 8px 15px; border-top-left-radius: 8px; border-top-right-radius: 8px; }
QProgressBar { border-radius: 7px; background: #262b42; border: 1px solid #414869; }
QProgressBar::chunk { background: #9274ed; border-radius: 6px; }
'''

# v2.0.9 premium sidebar/dashboard layout — visual only.
APP_STYLESHEET += r'''
QWidget#mainContent { background: #10101e; }
QFrame#appSidebar { background: #171328; border-right: 1px solid #493469; }
QLabel#sidebarBrand { background: transparent; color: #e6d5ff; font-size: 22px; font-weight: 850; padding: 8px 4px; }
QLabel#sidebarFooter { color: #8f82ae; font-size: 9px; letter-spacing: 0.5px; padding: 8px 4px; }
QPushButton#sidebarItem { text-align: left; background: transparent; color: #c9c0df; border: 1px solid transparent; border-radius: 11px; padding: 9px 10px; font-size: 13px; }
QPushButton#sidebarItem:hover { background: #302146; color: #f4eaff; border-color: #63418d; }
QPushButton#sidebarItem[selected="true"] { background: #513079; color: #ffffff; border: 1px solid #a96ae6; font-weight: 700; }
QFrame#browserCard, QFrame#transferCard, QFrame#connectionPanel { background: #211b31; border: 1px solid #574177; border-radius: 16px; }
QFrame#appHeader { background: #181427; border: 1px solid #422f65; border-radius: 13px; }
QLabel#appTitle { font-size: 22px; color: #f1e4ff; font-weight: 800; }
QLabel#cardTitle { color: #e4caff; font-size: 14px; font-weight: 750; }
QLabel#sectionHeading { color: #e3ccff; font-size: 13px; font-weight: 700; }
QPushButton#primaryAction { background: #7f39bd; color: white; border: 1px solid #b166ef; border-radius: 10px; }
QPushButton#primaryAction:hover:enabled { background: #994bd2; }
QPushButton, QToolButton { border-radius: 10px; }
QTableWidget { background: #171427; alternate-background-color: #241d35; border-color: #493862; border-radius: 10px; }
QHeaderView::section { background: #312642; border-bottom: 1px solid #5b4675; color: #dfccf9; }
QTabWidget::pane { background: #1c172d; border-color: #544071; }
QTabBar::tab:selected { background: #6c368e; }
QProgressBar::chunk { background: #af55ec; }
QScrollBar::handle:vertical { background: #70508d; border-radius: 5px; }
'''

APP_STYLESHEET += r'''
QLabel#stageRibbon { border-radius: 11px; border: 1px solid #55416c; background: #2c203e; color: #e6d4ff; padding: 9px 16px; font-size: 12px; font-weight: 650; }
'''

# v2.1.2: streamlined sidebar to avoid overcrowding and resizing glitches.
APP_STYLESHEET += r'''
QFrame#appSidebar { background: #151124; border-right: 1px solid #41315f; }
QPushButton#sidebarItem { min-height: 26px; padding: 8px 11px; border-radius: 11px; }
QPushButton#sidebarItem[selected="true"] { background: #653b94; border: 1px solid #b581ed; }
QFrame#browserCard, QFrame#transferCard, QFrame#connectionPanel { background: #201a30; border: 1px solid #493762; border-radius: 15px; }
QProgressBar#jobProgress { border: 1px solid #53436f; border-radius: 7px; background: #292438; color: #f6eaff; font-size: 11px; min-height: 19px; }
QProgressBar#jobProgress::chunk { background: #a55de2; border-radius: 6px; }
QProgressBar#jobProgress[complete="true"]::chunk { background: #2aaf82; }
QLabel#footerConnection, QLabel#footerMetric { padding: 4px 9px; border-radius: 9px; background: #252038; color: #bdb3d8; border: 1px solid #3d3156; }
QLabel#footerConnection[online="true"] { color: #7ce0ac; border-color: #356553; }
QStatusBar { min-height: 31px; background: #161224; }
'''

# v2.1.1: Labels inherit an opaque QWidget background in Qt when the global
# QMainWindow, QWidget selector paints all descendants. Keep plain captions
# transparent so rounded cards remain continuous; retain explicit badge/ribbon
# backgrounds via their more-specific object-name selectors above.
APP_STYLESHEET += r'''
QLabel { background-color: transparent; border: none; }
QLabel#appTitle, QLabel#subTitle, QLabel#sectionHeading, QLabel#cardTitle,
QLabel#sidebarFooter, QLabel#emptyState, QLabel#mutedLabel {
    background-color: transparent; border: none;
}
QTableCornerButton::section { background: #312642; border: none; border-bottom: 1px solid #5b4675; }
QHeaderView { background: #312642; }
'''

# v2.1.3: predictable compact transfer settings and workflow controls.
APP_STYLESHEET += r'''
QWidget#advancedPanel { background: #211a31; border: 1px solid #4e3b65; border-radius: 10px; }
QWidget#advancedPanel QLabel { background: transparent; color: #d3c1ea; font-size: 11px; }
QFrame#transferCard QToolButton { padding: 7px 11px; }
QFrame#transferCard QSpinBox, QFrame#transferCard QComboBox { min-height: 24px; }
'''
