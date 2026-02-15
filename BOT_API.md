# Bot API (TCP JSON)

Ниже описан отдельный API для ботов. Все запросы выполняются по TCP на адрес сервера и порт `5555`.  
Каждый запрос и ответ — одна строка JSON, заканчивается `\n`.

## Создание бота

**Кто может создать:** любой пользователь, имеющий `PassCodeHash`.

**Запрос:**

- `action`: `"bot_create"`
- `PassCodeHash` (или `passcode` / `passcode_hash`)
- `bot_name`: имя бота, **обязательно** заканчивается на `_bot`
- `bot_username`: необязательный ник (пустая строка = нет)
- `recovery_password`: 6‑значный пароль (строго 6 цифр)

**Ответ:**

- `status`: `"ok"` или `"error"`
- `message`: описание ошибки (если `status="error"`)
- `token`: токен бота (если `status="ok"`)

Пример запроса:

```json
{"action":"bot_create","PassCodeHash":"...","bot_name":"weather_bot","bot_username":"WeatherBot","recovery_password":"123456"}
```

Пример успешного ответа:

```json
{"type":"bot_create","status":"ok","token":"..."}
```

## Отправка сообщений от бота

**Запрос:**

- `action`: `"bot_send"`
- `token`: токен бота
- `to`: логин получателя
- `message`: текст сообщения
- `reply_to`: (необязательно) id сообщения, на которое отвечаете

**Ответ:**

- `status`: `"ok"` или `"error"`
- `message`: описание ошибки (если `status="error"`)

Пример:

```json
{"action":"bot_send","token":"...","to":"alice","message":"Привет!"}
```

## Получение накопившихся сообщений

Чтобы получить все накопившиеся сообщения, нужно указать токен и команду **точно**:
`"вышли все накопившиеся"`.

**Запрос:**

- `action`: `"bot_fetch"`
- `token`: токен бота
- `command`: `"вышли все накопившиеся"`

**Ответ:**

- `status`: `"ok"` или `"error"`
- `message`: описание ошибки (если `status="error"`)
- `items`: список сообщений (если `status="ok"`)

Пример:

```json
{"action":"bot_fetch","token":"...","command":"вышли все накопившиеся"}
```

Сообщения возвращаются в формате, как в истории DM:

```json
{
  "id": "...",
  "username": "sender",
  "to": "your_bot_name",
  "message": "text",
  "timestamp": "YYYY-MM-DD HH:MM:SS",
  "type": "dm",
  "delivered": true,
  "read": false
}
```
