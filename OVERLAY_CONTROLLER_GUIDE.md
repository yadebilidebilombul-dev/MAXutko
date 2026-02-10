# OverlayController - Руководство по использованию

## Описание

`OverlayController` - это класс PyQt5 для создания модальных окон с красивой анимацией. Окно появляется с размытием фона, увеличением размера и увеличением прозрачности.

## Основные возможности

✅ **Модальное окно** - всё внимание пользователя на экран  
✅ **Размытие фона** - плавная анимация фонового размытия  
✅ **Масштабирование** - окно появляется с анимацией увеличения  
✅ **Прозрачность** - плавное появление модального окна  
✅ **Закрытие клавишей** - нажатие вне окна его закрывает  
✅ **Управление контентом** - легко менять содержимое окна  

## Базовое использование

### 1. Простой пример

```python
from PyQt5 import QtWidgets, QtCore
from overlay_utils import OverlayController

class MyDialog(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Привет, мир!"))
        btn = QtWidgets.QPushButton("Закрыть")
        layout.addWidget(btn)

# В главном окне приложения:
content = MyDialog()
overlay = OverlayController(main_window, main_window)
overlay.set_content(content)
overlay.show()
```

### 2. С параметрами размера и размытия

```python
# Окно размером 400x300, размытие фона 12px
content = MyDialog()
overlay = OverlayController(
    parent=main_window,
    content_widget=main_window,
    modal_size=QtCore.QSize(400, 300),
    blur_radius=12
)
overlay.set_content(content)
overlay.show()
```

## Параметры конструктора

```python
OverlayController(parent, content_widget, modal_size=None, blur_radius=8)
```

| Параметр | Тип | Описание |
|----------|-----|---------|
| `parent` | QWidget | Родительский widget (обычно главное окно) |
| `content_widget` | QWidget | Widget, к которому применяется размытие (обычно главное окно) |
| `modal_size` | QSize | Размер модального окна. По умолчанию QSize(400, 600) |
| `blur_radius` | int | Радиус размытия фона (0-20). По умолчанию 8 |

## Основные методы

### `show()`
Показывает модальное окно с анимацией.

```python
overlay = OverlayController(parent, parent)
overlay.set_content(my_widget)
overlay.show()
```

### `hide()`
Скрывает модальное окно с анимацией реверса.

```python
overlay.hide()
```

### `set_content(widget)`
Устанавливает содержимое для отображения в модальном окне.

```python
content = QtWidgets.QWidget()
overlay.set_content(content)
```

### `is_visible()`
Проверяет, видно ли модальное окно.

```python
if overlay.is_visible():
    print("Окно видно")
```

### `resize(rect=None)`
Переустанавливает размеры overlay. Вызывается автоматически при `show()`.

```python
overlay.resize()
```

## Примеры реальных случаев

### Пример 1: Форма создания группы

```python
class CreateGroupWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        
        title = QtWidgets.QLabel("Создание группы")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(title)
        
        label = QtWidgets.QLabel("Введите название")
        layout.addWidget(label)
        
        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("Название группы")
        layout.addWidget(self.name_input)
        
        self.private_checkbox = QtWidgets.QCheckBox("Сделать приватной?")
        layout.addWidget(self.private_checkbox)
        
        layout.addStretch()
        
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        self.create_btn = QtWidgets.QPushButton("Создать")
        btn_layout.addWidget(self.create_btn)
        layout.addLayout(btn_layout)

# Показываем форму
def show_create_group_dialog(main_window):
    content = CreateGroupWidget()
    overlay = OverlayController(
        parent=main_window,
        content_widget=main_window,
        modal_size=QtCore.QSize(340, 220),
        blur_radius=8
    )
    overlay.set_content(content)
    
    def on_create():
        name = content.name_input.text().strip()
        private = content.private_checkbox.isChecked()
        if name:
            print(f"Создана группа: {name} (приватная: {private})")
            overlay.hide()
    
    content.create_btn.clicked.connect(on_create)
    overlay.show()
```

### Пример 2: Подтверждение действия

```python
def show_confirmation_dialog(parent, title, message, on_ok):
    content = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(content)
    
    layout.addWidget(QtWidgets.QLabel(title))
    layout.addWidget(QtWidgets.QLabel(message))
    layout.addStretch()
    
    btn_layout = QtWidgets.QHBoxLayout()
    
    cancel_btn = QtWidgets.QPushButton("Отмена")
    ok_btn = QtWidgets.QPushButton("ОК")
    
    btn_layout.addStretch()
    btn_layout.addWidget(cancel_btn)
    btn_layout.addWidget(ok_btn)
    layout.addLayout(btn_layout)
    
    overlay = OverlayController(parent, parent, modal_size=QtCore.QSize(300, 150))
    overlay.set_content(content)
    
    def on_ok_clicked():
        on_ok()
        overlay.hide()
    
    ok_btn.clicked.connect(on_ok_clicked)
    cancel_btn.clicked.connect(overlay.hide)
    
    overlay.show()
```

### Пример 3: Настройки

```python
class SettingsWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        
        layout.addWidget(QtWidgets.QLabel("Тема"))
        combo = QtWidgets.QComboBox()
        combo.addItems(["Светлая", "Тёмная", "Авто"])
        layout.addWidget(combo)
        
        layout.addWidget(QtWidgets.QLabel("Язык"))
        lang_combo = QtWidgets.QComboBox()
        lang_combo.addItems(["Русский", "English"])
        layout.addWidget(lang_combo)
        
        layout.addStretch()
        
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QtWidgets.QPushButton("Сохранить")
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

# Использование
def open_settings(main_window):
    content = SettingsWidget()
    overlay = OverlayController(
        main_window, 
        main_window,
        modal_size=QtCore.QSize(350, 250)
    )
    overlay.set_content(content)
    overlay.show()
```

## Стилизация

### QSS стили поддерживаемые по умолчанию

```css
/* Темный фон overlay */
#overlay {
    background-color: rgba(0, 0, 0, 0.5);
}

/* Модальное окно */
#settingsModal {
    background-color: #f3f4f6;
    border-radius: 14px;
    border: 1px solid #e5e7eb;
}

/* Кнопка закрытия */
#settingsClose {
    background-color: transparent;
    color: #6b7280;
    font-size: 18px;
    border: none;
    padding: 0px;
}

#settingsClose:hover {
    color: #111827;
    background-color: #e5e7eb;
    border-radius: 4px;
}
```

## Эффекты и анимация

### Параметры анимации

- **Длительность**: 180ms (жестко закодировано для плавности)
- **Easing при показе**: OutCubic (плавное замедление)
- **Easing при скрытии**: InCubic (плавное ускорение)

### Какие эффекты применяются

1. **Blur Effect** - размутие фонового widget
2. **Opacity Effect** - изменение прозрачности модального окна
3. **Geometry Animation** - масштабирование позиции и размера окна

## Важные особенности

### ⚠️ QPainter ошибки - РЕШЕНО

Проблема: "QPainter::begin: A paint device can only be painted by one painter at a time"

**Решение реализовано в версии с исправлениями:**
- Blur effect создается только в методе `_animate()`
- Старый эффект полностью удаляется перед созданием нового
- Всё управление жизненным циклом эффектов безопасно

### 🔒 Безопасность удаления эффектов

```python
# Все операции удаления находятся в try-except блоках
try:
    self._blur_effect.deleteLater()
except RuntimeError:
    pass  # Widget уже удален или был пересоздан
```

### 🎯 Правильный порядок инициализации

```python
# Правильно:
content = MyWidget()
overlay = OverlayController(parent, parent, modal_size=size)
overlay.set_content(content)  # ОБЯЗАТЕЛЬНО перед show()
overlay.show()

# Неправильно:
overlay = OverlayController(parent, parent)
overlay.show()  # Контент не установлен, окно пусто
```

## Производительность

- **Размер окна**: рекомендуется не более 600x600px
- **Blur radius**: 8-12 оптимально, выше 20 замедляет рендеринг
- **Частота обновления**: при частых показах/скрытиях окна может быть небольшой лаг (нормально)

## Отладка

### Включить отладку (добавить в код)

```python
# Перед созданием overlay:
from PyQt5 import QtCore
QtCore.QLoggingCategory("qt.qpa.painting").setEnabled(QtCore.QtWarningMsg, True)
```

### Проверить видимость

```python
if overlay.is_visible():
    overlay.hide()
else:
    overlay.show()
```

## Совместимость

- **PyQt5**: ✅ Полная поддержка
- **Python**: 3.6+
- **Windows**: ✅
- **macOS**: ✅
- **Linux**: ✅

## Лицензия

Часть проекта MAXutko Messenger
