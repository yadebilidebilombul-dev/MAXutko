# Система автоматического обновления MAXutko

## Инструкция по настройке

### На Google Drive

Необходимо загрузить 2 файла:

1. **Updates.json** - информация о версии и изменениях:
```json
{
  "version": "2.1.1",
  "changelog": "новое описание обновления"
}
```

2. **MAXutko.exe** - сам файл обновления

### На сервере

Для синхронизации версии локально (опционально):

Файл `server/updates.json` содержит информацию о текущей версии (совпадает с Google Drive версией):
```json
{
  "version": "2.1.1",
  "changelog": "новое описание обновления"
}
```

### На клиенте

Откройте файл `app/client.py` и установите две прямые ссылки скачивания с Google Drive:

```python
# Строки ~37-42
UPDATES_JSON_URL = "https://drive.google.com/uc?id=JSON_FILE_ID&export=download"
UPDATE_EXE_URL = "https://drive.google.com/uc?id=EXE_FILE_ID&export=download"
```

**Как получить FILE_ID:**

1. Загрузите файл (Updates.json или MAXutko.exe) на Google Drive
2. Откройте файл в браузере
3. Скопируйте ID из URL: `https://drive.google.com/file/d/FILE_ID/view`
4. Вставьте в код: `https://drive.google.com/uc?id=FILE_ID&export=download`

**Пример:**
- JSON: `https://drive.google.com/uc?id=1a2b3c4d5e&export=download`
- EXE: `https://drive.google.com/uc?id=5f6g7h8i9j&export=download`

## Как это работает

1. Пользователь входит в аккаунт
2. Сервер отправляет информацию о версии (из локального updates.json)
3. Если версия отличается → показывается плашка с информацией об обновлении
4. При нажатии "Обновить":
   - Сначала скачивается Updates.json с Google Drive (для проверки текущей версии)
   - Затем скачивается MAXutko.exe с Google Drive
   - Файл сохраняется в папку `Загрузки`
   - На рабочем столе создается ярлык
   - Показывается сообщение об успехе

## Версионирование

Текущая версия клиента: определяется переменной `VersionBulid` в `app/client.py`

Для выпуска нового обновления:

1. Обновите версию в `VersionBulid` в `app/client.py`
2. Обновите версию в `server/updates.json`
3. Обновите версию в `Updates.json` на Google Drive
4. Пересоберите exe: `pyinstaller --clean --noconsole --onefile --name MAXutko --icon=app\icon.ico app\client.py --add-data "app\ui.qss;."`
5. Загрузите новый `MAXutko.exe` на Google Drive

## Примеры версионирования

- `2.1.1` → `2.1.2` - патч-версия (bugfixes)
- `2.1.x` → `2.2.0` - минор-версия (новые функции)
- `2.x.x` → `3.0.0` - мажор-версия (большие изменения)

## Платформы

- Windows (основная поддержка)
- Использует PowerShell для создания ярлыков
- Fallback на batch-файл если PowerShell недоступен
