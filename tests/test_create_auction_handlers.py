from __future__ import annotations

import pytest

from app.bot.handlers.create_auction import (
    create_description_collect_photo,
    create_photo_step,
    create_photos_done,
)
from app.bot.states.auction_create import AuctionCreateStates


class _DummyPhoto:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class _DummyMessage:
    def __init__(
        self,
        *,
        text: str | None = None,
        photo_file_id: str | None = None,
        media_group_id: str | None = None,
    ) -> None:
        self.text = text
        self.photo = [_DummyPhoto(photo_file_id)] if photo_file_id is not None else []
        self.media_group_id = media_group_id
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs) -> None:
        self.answers.append(text)


class _DummyCallback:
    def __init__(self, data: str, message: _DummyMessage | None = None) -> None:
        self.data = data
        self.message = message
        self.from_user = object()
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False, **_kwargs) -> None:
        self.answers.append((text, show_alert))


class _DummyState:
    def __init__(self) -> None:
        self.state = None
        self.data: dict[str, object] = {}

    async def clear(self) -> None:
        self.state = None
        self.data = {}

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, data: dict[str, object] | None = None, **kwargs) -> None:
        if isinstance(data, dict):
            self.data.update(data)
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)


@pytest.mark.asyncio
async def test_album_flow_prompts_description_once() -> None:
    state = _DummyState()
    await state.set_state(AuctionCreateStates.waiting_photo)

    first_photo = _DummyMessage(photo_file_id="photo-1", media_group_id="album-1")
    second_photo = _DummyMessage(photo_file_id="photo-2", media_group_id="album-1")
    await create_photo_step(first_photo, state)
    await create_photo_step(second_photo, state)

    callback_message = _DummyMessage()
    done_callback = _DummyCallback("create:photos:done", message=callback_message)
    await create_photos_done(done_callback, state)

    trailing_album_photo = _DummyMessage(photo_file_id="photo-3", media_group_id="album-1")
    await create_description_collect_photo(trailing_album_photo, state)

    assert state.state == AuctionCreateStates.waiting_description
    assert sum("Теперь отправьте описание лота." in answer for answer in callback_message.answers) == 1
    assert trailing_album_photo.answers == []


@pytest.mark.asyncio
async def test_done_without_photos_shows_alert() -> None:
    state = _DummyState()
    await state.set_state(AuctionCreateStates.waiting_photo)

    done_callback = _DummyCallback("create:photos:done", message=_DummyMessage())
    await create_photos_done(done_callback, state)

    assert done_callback.answers == [("Сначала добавьте хотя бы одно фото", True)]
    assert state.state == AuctionCreateStates.waiting_photo


@pytest.mark.asyncio
async def test_album_feedback_does_not_show_stale_single_count() -> None:
    state = _DummyState()
    await state.set_state(AuctionCreateStates.waiting_photo)

    first_photo = _DummyMessage(photo_file_id="photo-1", media_group_id="album-1")
    second_photo = _DummyMessage(photo_file_id="photo-2", media_group_id="album-1")

    await create_photo_step(first_photo, state)
    await create_photo_step(second_photo, state)

    assert len(first_photo.answers) == 1
    assert "Альбом принят. После отправки всех фото нажмите 'Готово'." in first_photo.answers[0]
    assert second_photo.answers == []


@pytest.mark.asyncio
async def test_single_photo_feedback_keeps_exact_counter() -> None:
    state = _DummyState()
    await state.set_state(AuctionCreateStates.waiting_photo)

    single_photo = _DummyMessage(photo_file_id="photo-1")
    await create_photo_step(single_photo, state)

    assert len(single_photo.answers) == 1
    assert "Фото добавлено (1/10). Отправьте еще или нажмите 'Готово'." in single_photo.answers[0]
