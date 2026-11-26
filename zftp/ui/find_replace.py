"""Find, highlight and replace text in the built-in editor."""

from bisect import bisect_left
from itertools import islice
import regex as re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QWidget

HIGHLIGHT_LIMIT = 10000


class FindReplace(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.query = QLineEdit()
        self.query.setPlaceholderText('Find')
        self.replacement = QLineEdit()
        self.replacement.setPlaceholderText('Replace with')
        self.case_sensitive = QCheckBox('Match case')
        self.whole_word = QCheckBox('Whole words')
        self.regular = QCheckBox('Regex')
        self.result = QLabel()
        self.result.setTextFormat(Qt.PlainText)
        previous = QPushButton('Previous')
        next_button = QPushButton('Next')
        self.replace_button = QPushButton('Replace')
        self.replace_all_button = QPushButton('Replace all')
        close = QPushButton('Close find')
        layout.addWidget(self.query, 0, 0, 1, 3)
        layout.addWidget(previous, 0, 3)
        layout.addWidget(next_button, 0, 4)
        layout.addWidget(close, 0, 5)
        layout.addWidget(self.case_sensitive, 1, 0)
        layout.addWidget(self.whole_word, 1, 1)
        layout.addWidget(self.regular, 1, 2)
        layout.addWidget(self.result, 1, 3, 1, 3)
        layout.addWidget(self.replacement, 2, 0, 1, 3)
        layout.addWidget(self.replace_button, 2, 3)
        layout.addWidget(self.replace_all_button, 2, 4, 1, 2)
        self.timer = QTimer(self, interval=110, singleShot=True)
        self.timer.timeout.connect(self.refresh)
        self.query.textChanged.connect(self.schedule)
        editor.textChanged.connect(self.schedule)
        for option in (self.case_sensitive, self.whole_word, self.regular):
            option.toggled.connect(self.schedule)
        self.query.returnPressed.connect(lambda: self.step(1))
        next_button.clicked.connect(lambda: self.step(1))
        previous.clicked.connect(lambda: self.step(-1))
        self.replace_button.clicked.connect(lambda: self.replace(False))
        self.replace_all_button.clicked.connect(lambda: self.replace(True))
        close.clicked.connect(self.close_find)
        self.shortcuts = []
        for keys in ('Ctrl+F', 'Ctrl+H'):
            shortcut = QShortcut(QKeySequence(keys), editor)
            shortcut.activated.connect(self.open_find)
            self.shortcuts.append(shortcut)
        self.hide()

    def open_find(self):
        self.setVisible(True)
        enabled = not self.editor.isReadOnly()
        for widget in (self.replacement, self.replace_button, self.replace_all_button):
            widget.setEnabled(enabled)
        self.query.setFocus()
        self.query.selectAll()
        self.refresh()

    def close_find(self):
        self.hide()
        self.editor.setExtraSelections([])
        self.editor.setFocus()

    def schedule(self, *_):
        if self.isVisible():
            self.timer.start()

    def pattern(self):
        if not self.query.text():
            return None
        text = self.query.text() if self.regular.isChecked() else re.escape(self.query.text())
        if self.whole_word.isChecked():
            text = r'(?<!\w)(?:' + text + r')(?!\w)'
        return re.compile(text, re.MULTILINE | (0 if self.case_sensitive.isChecked() else re.IGNORECASE))

    def matches(self):
        text = self.editor.toPlainText()
        pattern = self.pattern()
        return text, pattern.finditer(text, timeout=0.3) if pattern else iter(())

    @staticmethod
    def character_positions(text):
        if text.isascii():
            return range(len(text) + 1)
        result = [0]
        for character in text:
            result.append(result[-1] + (2 if ord(character) > 0xffff else 1))
        return result

    def refresh(self):
        if not self.isVisible():
            return
        selections = []
        try:
            text, matches = self.matches()
            positions = self.character_positions(text)
            for match in islice(matches, HIGHLIGHT_LIMIT):
                highlight = QTextEdit.ExtraSelection()
                highlight.cursor = self.editor.textCursor()
                highlight.cursor.setPosition(positions[match.start()])
                highlight.cursor.setPosition(positions[match.end()], QTextCursor.KeepAnchor)
                highlight.format.setBackground(QColor('#88673c'))
                highlight.format.setForeground(QColor('#ffffff'))
                selections.append(highlight)
            more = next(matches, None) is not None
            self.result.setText(f'{len(selections):,}{"+" if more else ""} matches')
        except (re.error, TimeoutError) as error:
            self.result.setText(str(error))
        self.editor.setExtraSelections(selections)

    def step(self, direction):
        try:
            text, matches = self.matches()
            positions = self.character_positions(text)
            cursor = self.editor.textCursor()
            anchor = bisect_left(positions, cursor.selectionEnd() if direction == 1 else cursor.selectionStart())
            first, chosen = None, None
            for match in matches:
                if first is None:
                    first = match
                if direction == 1 and (match.start() >= anchor and
                                       (match.end() > anchor or not cursor.hasSelection())):
                    chosen = match
                    break
                if direction == -1:
                    if match.end() <= anchor and match.start() < anchor:
                        chosen = match
                    first = match
            chosen = chosen or first
            if chosen:
                cursor.setPosition(positions[chosen.start()])
                cursor.setPosition(positions[chosen.end()], QTextCursor.KeepAnchor)
                self.editor.setTextCursor(cursor)
                self.editor.ensureCursorVisible()
        except (re.error, TimeoutError) as error:
            self.result.setText(str(error))

    def replace(self, all_matches):
        if self.editor.isReadOnly():
            return
        try:
            pattern = self.pattern()
            if not pattern:
                return
            text = self.editor.toPlainText()
            cursor = self.editor.textCursor()
            replacement = self.replacement.text() if self.regular.isChecked() else lambda _: self.replacement.text()
            if all_matches:
                updated, count = pattern.subn(replacement, text, timeout=0.3)
                cursor.select(QTextCursor.Document)
            else:
                positions = self.character_positions(text)
                start = bisect_left(positions, cursor.selectionStart())
                end = bisect_left(positions, cursor.selectionEnd())
                match = pattern.search(text, start, timeout=0.3)
                if match is None or match.span() != (start, end):
                    self.step(1)
                    return
                updated = match.expand(replacement) if self.regular.isChecked() else replacement(match)
                count = 1
            if count:
                cursor.beginEditBlock()
                cursor.insertText(updated)
                cursor.endEditBlock()
                self.editor.setTextCursor(cursor)
            self.refresh()
        except (re.error, TimeoutError) as error:
            self.result.setText(str(error))
