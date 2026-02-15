from PyQt5 import QtCore, QtWidgets


class OverlayController(QtCore.QObject):
    """
    Контроллер для отображения модальных окон с наложением и размытием фона.
    
    Пример использования:
        content = MyWidget()
        overlay = OverlayController(parent, content, modal_size=QtCore.QSize(400, 300))
        overlay.set_content(content)
        overlay.show()
    """
    def __init__(self, parent, content_widget, modal_size=None, blur_radius=8):
        super().__init__(parent)
        self.parent = parent
        self.content_widget = content_widget
        self.modal_size = modal_size or QtCore.QSize(400, 600)
        self.blur_radius = blur_radius
        self._anim_group = None
        self._blur_effect = None

        # Создаем overlay (темный фон)
        self.overlay = QtWidgets.QWidget(parent)
        self.overlay.setObjectName("overlay")
        self.overlay.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.overlay.hide()
        self.overlay.installEventFilter(self)

        overlay_layout = QtWidgets.QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.addStretch(1)

        # Модальное окно
        self.modal = QtWidgets.QFrame(self.overlay)
        self.modal.setObjectName("settingsModal")
        self.modal.setFixedSize(self.modal_size)
        modal_layout = QtWidgets.QVBoxLayout(self.modal)
        modal_layout.setContentsMargins(16, 16, 16, 16)
        modal_layout.setSpacing(8)

        # Кнопка закрытия
        close_row = QtWidgets.QHBoxLayout()
        close_row.addStretch(1)
        self.close_btn = QtWidgets.QPushButton("×")
        self.close_btn.setObjectName("settingsClose")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.hide)
        close_row.addWidget(self.close_btn, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)
        modal_layout.addLayout(close_row)

        # Контейнер для контента
        self.content_container = QtWidgets.QWidget(self.modal)
        self.content_layout = QtWidgets.QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        modal_layout.addWidget(self.content_container, 1)
        modal_layout.addStretch(0)

        overlay_layout.addWidget(self.modal, 0, QtCore.Qt.AlignHCenter)
        overlay_layout.addStretch(1)

        # Opacity effect для модального окна
        self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(self.modal)
        self._opacity_effect.setOpacity(0.0)
        self.modal.setGraphicsEffect(self._opacity_effect)

    def is_visible(self):
        """Проверяет, видно ли модальное окно"""
        return self.overlay.isVisible()

    def set_content(self, widget):
        """
        Устанавливает контент для отображения в модальном окне.
        
        Args:
            widget: QWidget для отображения внутри модального окна
        """
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            child = item.widget()
            if child:
                child.setParent(None)
        if widget is not None:
            widget.setParent(self.content_container)
            widget.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding
            )
            self.content_layout.addWidget(widget, 1)
            widget.show()

    def resize(self, rect=None):
        """Переустанавливает размеры overlay в соответствии с parent"""
        if rect is None:
            rect = self.parent.rect()
        self.overlay.setGeometry(rect)
        if self.overlay.isVisible():
            self.modal.setGeometry(self._final_rect())

    def show(self):
        """Показывает модальное окно с анимацией"""
        if self.overlay.isVisible():
            return
        self.resize()
        self._opacity_effect.setOpacity(0.0)
        self.modal.setGeometry(self._start_rect())
        self.overlay.show()
        self.overlay.raise_()
        self._animate(show=True)

    def hide(self):
        """Скрывает модальное окно с анимацией"""
        if not self.overlay.isVisible():
            return
        self._animate(show=False)

    def eventFilter(self, obj, event):
        """Закрывает модальное окно при клике вне его"""
        if obj is self.overlay and event.type() == QtCore.QEvent.MouseButtonPress:
            if not self.modal.geometry().contains(event.pos()):
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def _start_rect(self):
        """Вычисляет начальный размер модального окна для анимации"""
        final_rect = self._final_rect()
        scale = 0.92
        w = int(final_rect.width() * scale)
        h = int(final_rect.height() * scale)
        center = final_rect.center()
        return QtCore.QRect(0, 0, w, h).translated(center.x() - w // 2, center.y() - h // 2 + 10)

    def _final_rect(self):
        """Вычисляет финальный размер модального окна"""
        rect = self.overlay.rect()
        w = self.modal_size.width()
        h = self.modal_size.height()
        return QtCore.QRect(0, 0, w, h).translated(rect.center().x() - w // 2, rect.center().y() - h // 2)

    def _create_blur_effect(self):
        """Создает blur effect для background widget"""
        if self._blur_effect is not None:
            # Удаляем старый эффект перед созданием нового
            old_effect = self.content_widget.graphicsEffect()
            if old_effect is self._blur_effect:
                self.content_widget.setGraphicsEffect(None)
            try:
                self._blur_effect.deleteLater()
            except RuntimeError:
                pass
        
        self._blur_effect = QtWidgets.QGraphicsBlurEffect()
        self._blur_effect.setBlurRadius(0)
        self.content_widget.setGraphicsEffect(self._blur_effect)

    def _animate(self, show=True):
        """Запускает анимацию появления/исчезновения модального окна"""
        # Очищаем предыдущую анимацию
        if self._anim_group is not None:
            try:
                self._anim_group.finished.disconnect(self._cleanup_hide)
            except (TypeError, RuntimeError):
                pass
            try:
                self._anim_group.finished.disconnect(self._clear_anim_group)
            except (TypeError, RuntimeError):
                pass
            try:
                self._anim_group.stop()
            except RuntimeError:
                pass
            try:
                self._anim_group.deleteLater()
            except RuntimeError:
                pass
            self._anim_group = None

        self._anim_group = QtCore.QParallelAnimationGroup(self)

        # Создаем blur effect только при показе
        if show:
            self._create_blur_effect()

        # Анимация размытия фона
        blur_effect = self._blur_effect
        if blur_effect is not None:
            try:
                start_value = blur_effect.blurRadius()
            except RuntimeError:
                blur_effect = None
                self._blur_effect = None
        if blur_effect is not None:
            blur_anim = QtCore.QPropertyAnimation(blur_effect, b"blurRadius", self)
            blur_anim.setDuration(180)
            blur_anim.setStartValue(start_value)
            blur_anim.setEndValue(self.blur_radius if show else 0)

        # Анимация прозрачности модального окна
        opacity_anim = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity", self)
        opacity_anim.setDuration(180)
        opacity_anim.setStartValue(self._opacity_effect.opacity())
        opacity_anim.setEndValue(1.0 if show else 0.0)

        # Анимация размера модального окна
        geom_anim = QtCore.QPropertyAnimation(self.modal, b"geometry", self)
        geom_anim.setDuration(180)
        geom_anim.setStartValue(self.modal.geometry())
        geom_anim.setEndValue(self._final_rect() if show else self._start_rect())

        # Добавляем все анимации в группу
        if blur_effect is not None:
            blur_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic if show else QtCore.QEasingCurve.InCubic)
            self._anim_group.addAnimation(blur_anim)
        for anim in (opacity_anim, geom_anim):
            anim.setEasingCurve(QtCore.QEasingCurve.OutCubic if show else QtCore.QEasingCurve.InCubic)
            self._anim_group.addAnimation(anim)

        # Когда анимация завершена, очищаем ресурсы
        if not show:
            self._anim_group.finished.connect(self._cleanup_hide)
        self._anim_group.finished.connect(self._clear_anim_group)
        self._anim_group.start()

    def _clear_anim_group(self):
        """Удаляет группу анимации"""
        if self._anim_group is not None:
            self._anim_group.deleteLater()
        self._anim_group = None

    def _cleanup_hide(self):
        """Очищает эффекты после скрытия"""
        self.overlay.hide()
        # Удаляем blur effect с безопасностью
        if self._blur_effect is not None:
            if self.content_widget.graphicsEffect() is self._blur_effect:
                self.content_widget.setGraphicsEffect(None)
            try:
                self._blur_effect.deleteLater()
            except RuntimeError:
                pass
            self._blur_effect = None

