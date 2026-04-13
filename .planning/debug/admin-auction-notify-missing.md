---
status: resolved
trigger: "Так, почему у нас сейчас не приходят сообщения в админ-чат о новых аукционах, закрытых аукционов и т.д. Мы ведь это делали для удобства администрирования в одном чате"
created: 2026-02-26T10:25:08Z
updated: 2026-02-26T10:44:44Z
---

## Current Focus

hypothesis: Диагностика завершена; восстановление уведомлений и регресс-тесты подтверждены в runtime.
test: Зафиксировать финальный результат прогона pytest и отдать итог пользователю.
expecting: Стабильный зеленый результат целевого набора тестов.
next_action: Сообщить результат и предложить следующий шаг (коммит по запросу).

## Symptoms

expected: Сообщения о новых и закрытых аукционах приходят в единый админ-чат по темам.
actual: Такие сообщения не приходят.
errors: Явных ошибок в репорте пользователя нет.
reproduction: Создать/опубликовать аукцион и дождаться закрытия (или закрыть вручную) — в мод-чате нет системного уведомления.
started: Не указано.

## Eliminated

- hypothesis: Не настроены секции moderation topics для аукционов.
  evidence: В settings есть moderation_topic_auctions_active_id/frozen_id/closed_id и enum секции AUCTIONS_ACTIVE/FROZEN/CLOSED.
  timestamp: 2026-02-26T10:25:08Z

## Evidence

- timestamp: 2026-02-26T10:25:08Z
  checked: app/services/moderation_topic_router.py
  found: Реализованы секции AUCTIONS_ACTIVE/AUCTIONS_FROZEN/AUCTIONS_CLOSED и отправка через send_section_message.
  implication: Инфраструктура роутинга готова.

- timestamp: 2026-02-26T10:25:08Z
  checked: app/bot/handlers/publish_auction.py
  found: После публикации нет вызова send_section_message в moderation чат.
  implication: Новые активные аукционы не сигнализируются администраторам.

- timestamp: 2026-02-26T10:25:08Z
  checked: app/services/auction_service.py finalize_expired_auctions
  found: После авто-завершения шлются только user-topic уведомления продавцу/победителю.
  implication: Автозакрытия не попадают в админ-чат.

- timestamp: 2026-02-26T10:25:08Z
  checked: app/bot/handlers/moderation.py (/freeze /unfreeze /end)
  found: После мод-действий шлются только user-topic уведомления участникам.
  implication: Модераторские изменения статуса аукциона не попадают в админ-чат по темам.

- timestamp: 2026-02-26T10:29:39Z
  checked: app/bot/handlers/publish_auction.py
  found: Добавлен send_section_message в AUCTIONS_ACTIVE после успешной публикации; прикладывается кнопка на пост при наличии ссылки.
  implication: Публикация нового активного лота теперь уведомляет админ-чат.

- timestamp: 2026-02-26T10:29:39Z
  checked: app/services/auction_service.py и app/bot/handlers/bid_actions.py
  found: Добавлены уведомления в AUCTIONS_CLOSED для авто-завершения по таймеру и завершения выкупом.
  implication: Закрытия больше не теряются для модераторов.

- timestamp: 2026-02-26T10:29:39Z
  checked: app/bot/handlers/moderation.py
  found: Добавлены уведомления lifecycle в AUCTIONS_FROZEN/AUCTIONS_ACTIVE/AUCTIONS_CLOSED для команд и callback-сценариев панели.
  implication: Ручные модераторские смены статуса аукциона снова централизованы в админ-чате.

- timestamp: 2026-02-26T10:29:39Z
  checked: локальная валидация
  found: `python -m py_compile` на измененных файлах проходит; `pytest`/`ruff` недоступны в текущем окружении (модули не установлены).
  implication: Синтаксис валиден, но полный прогон тестов нужно выполнить в проектном runtime.

- timestamp: 2026-02-26T10:33:57Z
  checked: tests/integration/test_publish_auction_flow.py
  found: Добавлена проверка, что publish-path вызывает send_section_message в секцию AUCTIONS_ACTIVE.
  implication: Регресс по отсутствию admin-уведомления при публикации теперь ловится тестом.

- timestamp: 2026-02-26T10:33:57Z
  checked: tests/test_bid_actions_outbid_notifications.py
  found: Добавлен тест _notify_auction_finish -> send_section_message с секцией AUCTIONS_CLOSED.
  implication: Регресс по отсутствию admin-уведомления при завершении выкупом теперь ловится тестом.

- timestamp: 2026-02-26T10:33:57Z
  checked: python -m py_compile tests/integration/test_publish_auction_flow.py tests/test_bid_actions_outbid_notifications.py
  found: Синтаксических ошибок нет.
  implication: Новые тесты валидны на уровне импорта/синтаксиса.

- timestamp: 2026-02-26T10:40:34Z
  checked: docker-run pytest с RUN_INTEGRATION_TESTS=1
  found: 1 failed, 7 passed; падение в test_publish_command_activates_auction_and_persists_post из-за AttributeError у sent_message.chat.username в тестовом SimpleNamespace.
  implication: Нужно обновить тестовый стаб под новый контракт publish handler.

- timestamp: 2026-02-26T10:42:11Z
  checked: повторный docker-run pytest после частичной правки
  found: та же ошибка сохраняется, но источник уточнен — _DummyBot.send_photo возвращает chat без username.
  implication: требуется финальная корректировка тестового double, после чего expected pass.

- timestamp: 2026-02-26T10:44:44Z
  checked: финальный docker-run pytest (RUN_INTEGRATION_TESTS=1, TEST_DATABASE_URL=.../auction_test)
  found: 8 passed, 0 failed для tests/test_bid_actions_outbid_notifications.py и tests/integration/test_publish_auction_flow.py.
  implication: Исправление и новые regression-тесты подтверждены в docker runtime.

- timestamp: 2026-02-26T10:44:44Z
  checked: рабочее дерево после тестов
  found: Побочный артефакт lite_auction_bot.egg-info/SOURCES.txt восстановлен через git restore.
  implication: В изменениях остались только целевые source/test/debug файлы.

## Resolution

root_cause: Lifecycle события аукционов (publish/freeze/unfreeze/end/auto-close) не интегрированы с send_section_message и секциями auctions_*.
fix: Добавлены вызовы send_section_message для auction lifecycle (publish, buyout close, auto-close, freeze/unfreeze/end и panel callback freeze/unfreeze) с маршрутизацией в AUCTIONS_ACTIVE/AUCTIONS_FROZEN/AUCTIONS_CLOSED; добавлены regression-тесты на publish и buyout-close маршруты.
verification: Пройден runtime-прогон в docker: `RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://auction:auction@db:5432/auction_test python -m pytest -q tests/test_bid_actions_outbid_notifications.py tests/integration/test_publish_auction_flow.py` => 8 passed.
files_changed: [app/bot/handlers/publish_auction.py, app/services/auction_service.py, app/bot/handlers/bid_actions.py, app/bot/handlers/moderation.py, tests/integration/test_publish_auction_flow.py, tests/test_bid_actions_outbid_notifications.py]
