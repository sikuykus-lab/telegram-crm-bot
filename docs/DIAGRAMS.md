# Диаграммы

Три вида схемы — как в [dataroom-cms](https://github.com/sikuykus-lab/dataroom-cms):
**данные**, **взаимодействие пользователя**, **процессы администратора**.

Рендер: скопировать блок в [mermaid.live](https://mermaid.live).

## Схема данных

```mermaid
flowchart TB
  subgraph inbound ["Вход"]
    WEB["Lead API\nс сайта"]
    LS["ЛС клиента"]
  end

  subgraph crm ["CRM Bot"]
    BOT["main.py"]
    DB["LEAD + STATUS_HISTORY"]
  end

  subgraph team ["Команда"]
    GRP["Форум-группа"]
    MGR["Менеджер"]
  end

  WEB --> GRP
  LS --> BOT
  BOT --> DB
  BOT --> GRP
  MGR --> BOT
```

## Процесс пользователя

```mermaid
flowchart LR
  A["Заявка с сайта\nили ЛС"] --> B["Топик в группе"]
  B --> C["Менеджер ведёт\nстатус"]
  C --> D["Закрыт / отказ"]
```

## Процессы администратора

```mermaid
flowchart TD
  R1[".env + SQLite"] --> R2["systemctl telegram-crm-bot"]
  R2 --> R3["Статусы лида\nв боте и группе"]
  R3 --> R4["scheduler follow-up\n+ SITE_URL для Lead API"]
```
