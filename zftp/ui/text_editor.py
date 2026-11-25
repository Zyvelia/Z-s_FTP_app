"""Line navigation, gutter and syntax highlighting for the text viewer."""

import re
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QPainter, QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QInputDialog, QPlainTextEdit, QWidget


class LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        editor = self.editor
        painter = QPainter(self)
        painter.fillRect(event.rect(), editor.palette().alternateBase())
        painter.setPen(editor.palette().text().color())
        block = editor.firstVisibleBlock()
        while block.isValid():
            top = round(editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top())
            if top > event.rect().bottom():
                break
            if block.isVisible() and top + editor.blockBoundingRect(block).height() >= event.rect().top():
                painter.drawText(0, top, self.width() - 4, editor.fontMetrics().height(),
                                 Qt.AlignRight, str(block.blockNumber() + 1))
            block = block.next()


class TextEditor(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.gutter = LineNumbers(self)
        self.blockCountChanged.connect(self.update_gutter)
        self.updateRequest.connect(self.update_numbers)
        self.line_shortcut = QShortcut(QKeySequence('Ctrl+G'), self)
        self.line_shortcut.activated.connect(self.go_to_line)
        self.update_gutter()

    def update_gutter(self, *_):
        width = 10 + self.fontMetrics().horizontalAdvance('9') * max(2, len(str(self.blockCount())))
        self.setViewportMargins(width, 0, 0, 0)
        bounds = self.contentsRect()
        self.gutter.setGeometry(QRect(bounds.left(), bounds.top(), width, bounds.height()))

    def update_numbers(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_gutter()

    def go_to_line(self):
        line, accepted = QInputDialog.getInt(self, 'Go to line', 'Line number', self.textCursor().blockNumber() + 1,
                                              1, self.blockCount())
        if accepted:
            self.setTextCursor(QTextCursor(self.document().findBlockByNumber(line - 1)))
            self.centerCursor()
            self.setFocus()


class TextHighlighter(QSyntaxHighlighter):
    def __init__(self, document, name):
        super().__init__(document)
        suffix = Path(name).suffix.lower()
        strings = r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'"""
        number = r'\b(?:0x[\da-fA-F]+|\d+(?:\.\d+)?)\b'
        keyword = r'\b(?:true|false|null|if|else|elif|for|while|return|function|class|const|let|var|def|from|import|echo|exit|in|try|catch|raise|SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|BEGIN|END)\b'
        if suffix in ('.ini', '.cfg', '.conf', '.toml', '.reg', '.properties', '.yaml', '.yml') or name.startswith('.'):
            patterns = [(r'^\s*\[.+?\]\s*$', '#d9b3ff'), (r'^\s*[^#;:=]+(?=[=:])', '#8cc9ff'),
                        (number, '#ffca91'), (strings + r'|[;#].*$', '#a4d9a4')]
        elif suffix in ('.xml', '.html', '.htm', '.svg', '.plist'):
            patterns = [(r'</?[\w:.-]+|/?>', '#8cc9ff'), (r'[\w:.-]+(?=\s*=)', '#d9b3ff'),
                        (strings + r'|<!--.*?-->', '#a4d9a4')]
        elif suffix in ('.json', '.jsonl', '.ndjson', '.py', '.sh', '.bash', '.ps1', '.js', '.ts', '.lua',
                        '.c', '.h', '.cpp', '.hpp', '.cs', '.java', '.go', '.rs', '.sql', '.bat', '.cmd'):
            comment = (r'|#.*$' if suffix in ('.py', '.sh', '.bash', '.ps1') else
                       r'|--.*$' if suffix in ('.lua', '.sql') else r'|//.*$' if suffix not in ('.json', '.jsonl', '.ndjson') else '')
            patterns = [(number, '#ffca91'), (keyword, '#d9b3ff'), (strings + comment, '#a4d9a4')]
        elif suffix in ('.md', '.rst'):
            patterns = [(r'^#{1,6}\s.*$', '#d9b3ff')]
        else:
            patterns = []
        self.rules = []
        for pattern, color in patterns:
            style = QTextCharFormat()
            style.setForeground(QColor(color))
            self.rules.append((re.compile(pattern), style))

    def highlightBlock(self, text):
        if len(text) > 16384:
            return
        # Qt indexes UTF-16 code units rather than Python code points.
        offsets = [0]
        if not text.isascii():
            for character in text:
                offsets.append(offsets[-1] + (2 if ord(character) > 0xffff else 1))
        for regex, style in self.rules:
            for match in regex.finditer(text):
                start, end = match.span()
                if len(offsets) > 1:
                    start, end = offsets[start], offsets[end]
                self.setFormat(start, end - start, style)
