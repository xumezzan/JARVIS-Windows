"""Manual browser controls using only observed tab/element snapshots."""

from html import escape

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from jarvis.tools.browser import BrowserResult, ElementTarget, PageTarget, PageView


class BrowserControls(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.tool = ""
        self.views: dict[str, PageView] = {}
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Отдельный анонимный сеанс без JavaScript. Поддерживаются статические страницы "
            "и поиск через GET-формы; отправка POST отключена. Вкладки и введённый текст "
            "удаляются при закрытии этого окна."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.url = QLineEdit("https://example.com/")
        self.url.setMaxLength(6000)
        self.tabs = QComboBox()
        self.tabs.setMinimumContentsLength(20)
        self.tabs.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.elements = QComboBox()
        self.text = QPlainTextEdit()
        self.text.setMaximumHeight(95)
        self.form.addRow("Адрес", self.url)
        self.form.addRow("Наблюдённая вкладка", self.tabs)
        self.form.addRow("Элемент страницы", self.elements)
        self.form.addRow("Текст / запрос поиска", self.text)
        layout.addLayout(self.form)
        self.observation = QPlainTextEdit()
        self.observation.setReadOnly(True)
        self.observation.setMaximumHeight(210)
        self.observation.setPlaceholderText(
            "Прочитанный текст страницы появится здесь (до 12 000 символов)."
        )
        layout.addWidget(self.observation)
        self.tabs.currentIndexChanged.connect(self.refresh_elements)

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.setVisible(tool.startswith("browser."))
        self.form.setRowVisible(self.url, tool in ("browser.open", "browser.navigate"))
        self.form.setRowVisible(
            self.tabs,
            tool
            in (
                "browser.navigate",
                "browser.read",
                "browser.type",
                "browser.click",
                "browser.close",
            ),
        )
        self.form.setRowVisible(self.elements, tool in ("browser.type", "browser.click"))
        self.form.setRowVisible(self.text, tool in ("browser.search", "browser.type"))
        self.refresh_elements()

    @Slot()
    def refresh_elements(self) -> None:
        self.elements.clear()
        target = self.tabs.currentData()
        if not isinstance(target, PageTarget):
            return
        self.tabs.setToolTip("<pre>" + escape(target.url) + "</pre>")
        view = self.views.get(target.tab_id)
        if view is None or view.target != target:
            return
        for element in view.elements:
            if (self.tool == "browser.type" and element.role == "textbox") or (
                self.tool == "browser.click" and element.request is not None
            ):
                self.elements.addItem(f"{element.role}: {element.name}", element)

    def arguments(self) -> dict[str, object]:
        tool = self.tool
        if tool == "browser.open":
            return {"url": self.url.text()}
        if tool == "browser.search":
            return {"query": self.text.toPlainText()}
        if tool == "browser.get_tabs":
            return {}
        target = self.tabs.currentData()
        if not isinstance(target, PageTarget):
            raise ValueError("Сначала откройте страницу или получите список вкладок.")
        arguments: dict[str, object] = {"target": target.model_dump()}
        if tool == "browser.navigate":
            arguments["url"] = self.url.text()
        if tool in ("browser.type", "browser.click"):
            element = self.elements.currentData()
            if not isinstance(element, ElementTarget):
                raise ValueError("Прочитайте страницу и выберите поддерживаемый элемент.")
            arguments["element"] = element.model_dump()
        if tool == "browser.type":
            arguments["text"] = self.text.toPlainText()
        return arguments

    def accept_result(self, tool: str, result: BrowserResult) -> str:
        targets = [self.tabs.itemData(index) for index in range(self.tabs.count())]
        selected = self.tabs.currentData()
        preferred = selected.tab_id if isinstance(selected, PageTarget) else None
        if result.page is not None:
            view = result.page
            self.views[view.target.tab_id] = view
            targets = [target for target in targets if target.tab_id != view.target.tab_id]
            targets.append(view.target)
            preferred = view.target.tab_id
            self.observation.setPlainText(f"{view.title}\n{view.target.url}\n\n{view.text}")
        if tool == "browser.get_tabs":
            targets = result.tabs
        if result.closed_tab is not None:
            targets = [target for target in targets if target.tab_id != result.closed_tab]
            self.views.pop(result.closed_tab, None)
            self.observation.clear()
        self.tabs.clear()
        for target in targets:
            self.tabs.addItem(target.url, target)
            if target.tab_id == preferred:
                self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self.refresh_elements()
        if tool == "browser.type":
            return "SUCCESS — поле содержит подтверждённый текст."
        if tool == "browser.close":
            return "SUCCESS — выбранная вкладка сеанса закрыта."
        if tool == "browser.get_tabs":
            return f"SUCCESS — вкладок в сеансе: {len(targets)}."
        return "SUCCESS — страница прочитана, результат проверен."
