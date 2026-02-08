from PyQt5 import QtCore, QtWidgets


class OverlayController(QtCore.QObject):
    def __init__(self, parent, content_widget, modal_size=None, blur_radius=8):
        super().__init__(parent)
        self.parent = parent
        self.content_widget = content_widget
        self.modal_size = modal_size or QtCore.QSize(400, 600)
        self.blur_radius = blur_radius
        self._anim_group = None

        self.overlay = QtWidgets.QWidget(parent)
        self.overlay.setObjectName("overlay")
        self.overlay.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.overlay.hide()
        self.overlay.installEventFilter(self)

        overlay_layout = QtWidgets.QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.addStretch(1)

        self.modal = QtWidgets.QFrame(self.overlay)
        self.modal.setObjectName("settingsModal")
        self.modal.setFixedSize(self.modal_size)
        modal_layout = QtWidgets.QVBoxLayout(self.modal)
        modal_layout.setContentsMargins(16, 16, 16, 16)
        modal_layout.setSpacing(8)

        close_row = QtWidgets.QHBoxLayout()
        close_row.addStretch(1)
        self.close_btn = QtWidgets.QPushButton("×")
        self.close_btn.setObjectName("settingsClose")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.hide)
        close_row.addWidget(self.close_btn, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)
        modal_layout.addLayout(close_row)
        self.content_container = QtWidgets.QWidget(self.modal)
        self.content_layout = QtWidgets.QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        modal_layout.addWidget(self.content_container, 1)
        modal_layout.addStretch(0)

        overlay_layout.addWidget(self.modal, 0, QtCore.Qt.AlignHCenter)
        overlay_layout.addStretch(1)

        self._blur_effect = None

        self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(self.modal)
        self._opacity_effect.setOpacity(0.0)
        self.modal.setGraphicsEffect(self._opacity_effect)

    def is_visible(self):
        return self.overlay.isVisible()

    def set_content(self, widget):
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
        if rect is None:
            rect = self.parent.rect()
        self.overlay.setGeometry(rect)
        if self.overlay.isVisible():
            self.modal.setGeometry(self._final_rect())

    def show(self):
        if self.overlay.isVisible():
            return
        self.resize()
        if self._blur_effect is None:
            self._blur_effect = QtWidgets.QGraphicsBlurEffect(self.content_widget)
            self._blur_effect.setBlurRadius(0)
        self.content_widget.setGraphicsEffect(self._blur_effect)
        self._opacity_effect.setOpacity(0.0)
        self.modal.setGeometry(self._start_rect())
        self.overlay.show()
        self.overlay.raise_()
        self._animate(show=True)

    def hide(self):
        if not self.overlay.isVisible():
            return
        self._animate(show=False)

    def eventFilter(self, obj, event):
        if obj is self.overlay and event.type() == QtCore.QEvent.MouseButtonPress:
            if not self.modal.geometry().contains(event.pos()):
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def _start_rect(self):
        final_rect = self._final_rect()
        scale = 0.92
        w = int(final_rect.width() * scale)
        h = int(final_rect.height() * scale)
        center = final_rect.center()
        return QtCore.QRect(0, 0, w, h).translated(center.x() - w // 2, center.y() - h // 2 + 10)

    def _final_rect(self):
        rect = self.overlay.rect()
        w = self.modal_size.width()
        h = self.modal_size.height()
        return QtCore.QRect(0, 0, w, h).translated(rect.center().x() - w // 2, rect.center().y() - h // 2)

    def _animate(self, show=True):
        if self._anim_group is not None:
            try:
                self._anim_group.stop()
            except RuntimeError:
                pass
        self._anim_group = QtCore.QParallelAnimationGroup(self)

        blur_anim = QtCore.QPropertyAnimation(self._blur_effect, b"blurRadius", self)
        blur_anim.setDuration(180)
        blur_anim.setStartValue(self._blur_effect.blurRadius())
        blur_anim.setEndValue(self.blur_radius if show else 0)

        opacity_anim = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity", self)
        opacity_anim.setDuration(180)
        opacity_anim.setStartValue(self._opacity_effect.opacity())
        opacity_anim.setEndValue(1.0 if show else 0.0)

        geom_anim = QtCore.QPropertyAnimation(self.modal, b"geometry", self)
        geom_anim.setDuration(180)
        geom_anim.setStartValue(self.modal.geometry())
        geom_anim.setEndValue(self._final_rect() if show else self._start_rect())

        for anim in (blur_anim, opacity_anim, geom_anim):
            anim.setEasingCurve(QtCore.QEasingCurve.OutCubic if show else QtCore.QEasingCurve.InCubic)
            self._anim_group.addAnimation(anim)

        if not show:
            self._anim_group.finished.connect(self._cleanup_hide)
        self._anim_group.finished.connect(self._clear_anim_group)
        self._anim_group.start()

    def _clear_anim_group(self):
        if self._anim_group is not None:
            self._anim_group.deleteLater()
        self._anim_group = None

    def _cleanup_hide(self):
        self.overlay.hide()
        self.content_widget.setGraphicsEffect(None)
        if self._blur_effect is not None:
            try:
                self._blur_effect.deleteLater()
            except RuntimeError:
                pass
        self._blur_effect = None
