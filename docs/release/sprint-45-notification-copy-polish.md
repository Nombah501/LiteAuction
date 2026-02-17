# Sprint 45 Notification Copy Polish

## Goal

Keep notification copy concise, consistent, and easier to scan in private chat.

## Updated Templates

- Outbid:
  - `Лот #<id>: вашу ставку перебили.`
- Outbid digest:
  - `Дайджест по лоту #<id>: за <window> ставку перебивали <count> раз.`
- Finish/win:
  - `Лот #<id> завершен выкупом.`
  - `Вы выиграли лот #<id> (выкуп).`
  - `Лот #<id> завершен.`
  - `Вы выиграли лот #<id>.`
- Moderation notifications:
  - unified prefix `Модерация:` for freeze/unfreeze/finish/winner/bid-removed outcomes

## Action Labels

- `Открыть аукцион` -> `Открыть пост лота`
- `Отключить этот тип` -> `Отключить тип уведомлений`
- `Пауза по лоту на 1ч` -> `Пауза по лоту на 1 ч`

## Test Coverage

- Copy template unit tests in `tests/test_notification_copy_service.py`
- Keyboard label assertions updated in:
  - `tests/test_auction_post_links.py`
  - `tests/test_private_topics_notification_markup.py`
