from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.auction import (
    anti_sniper_keyboard,
    buyout_choice_keyboard,
    draft_publish_keyboard,
    duration_keyboard,
)
from app.bot.states.auction_create import AuctionCreateStates
from app.db.session import SessionFactory
from app.services.moderation_service import is_tg_user_blacklisted
from app.services.auction_service import create_draft_auction, load_auction_view, render_auction_caption
from app.services.publish_gate_service import evaluate_seller_publish_gate
from app.services.user_service import upsert_user

router = Router(name="create_auction")


def _parse_usd_amount(text: str) -> int | None:
    raw = text.strip().replace("$", "")
    if not raw.isdigit():
        return None
    amount = int(raw)
    if amount < 1:
        return None
    return amount


async def _prompt_min_step(target: Message | CallbackQuery) -> None:
    if isinstance(target, Message):
        await target.answer("Укажите минимальный шаг ставки в USD (например: 1 или 5).")
        return

    if target.message is not None:
        await target.message.answer("Укажите минимальный шаг ставки в USD (например: 1 или 5).")


@router.message(Command("newauction"), F.chat.type == ChatType.PRIVATE)
async def command_new_auction(message: Message, state: FSMContext) -> None:
    if message.from_user is not None:
        async with SessionFactory() as session:
            if await is_tg_user_blacklisted(session, message.from_user.id):
                await message.answer("Вы в черном списке и не можете создавать аукционы")
                return

    await state.clear()
    await state.set_state(AuctionCreateStates.waiting_photo)
    await message.answer("Отправьте фото лота.")


@router.callback_query(F.data == "create:new")
async def callback_new_auction(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is not None:
        async with SessionFactory() as session:
            if await is_tg_user_blacklisted(session, callback.from_user.id):
                await callback.answer("Вы в черном списке", show_alert=True)
                return

    await state.clear()
    await state.set_state(AuctionCreateStates.waiting_photo)
    await callback.answer()
    if callback.message:
        await callback.message.answer("Отправьте фото лота.")


@router.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
async def cancel_creation(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Создание аукциона отменено. Для нового лота нажмите /newauction")


@router.message(AuctionCreateStates.waiting_photo, F.photo)
async def create_photo_step(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    await state.update_data(photo_file_id=photo.file_id)
    await state.set_state(AuctionCreateStates.waiting_description)
    await message.answer("Отлично. Теперь отправьте описание лота.")


@router.message(AuctionCreateStates.waiting_photo)
async def create_photo_step_invalid(message: Message) -> None:
    await message.answer("Нужно отправить именно фото лота.")


@router.message(AuctionCreateStates.waiting_description, F.text)
async def create_description_step(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    if len(description) < 3:
        await message.answer("Описание слишком короткое. Добавьте больше деталей.")
        return

    await state.update_data(description=description)
    await state.set_state(AuctionCreateStates.waiting_start_price)
    await message.answer("Укажите начальную цену в USD (целое число, минимум 1).")


@router.message(AuctionCreateStates.waiting_description)
async def create_description_step_invalid(message: Message) -> None:
    await message.answer("Пришлите описание текстом.")


@router.message(AuctionCreateStates.waiting_start_price, F.text)
async def create_start_price_step(message: Message, state: FSMContext) -> None:
    amount = _parse_usd_amount(message.text)
    if amount is None:
        await message.answer("Некорректная цена. Введите целое число USD, минимум 1.")
        return

    await state.update_data(start_price=amount)
    await state.set_state(AuctionCreateStates.waiting_buyout_price)
    await message.answer(
        "Укажите цену выкупа в USD или нажмите 'Пропустить'.\n"
        "Цена выкупа не может быть ниже стартовой.",
        reply_markup=buyout_choice_keyboard(),
    )


@router.message(AuctionCreateStates.waiting_start_price)
async def create_start_price_step_invalid(message: Message) -> None:
    await message.answer("Введите стартовую цену текстом (например: 100).")


@router.callback_query(AuctionCreateStates.waiting_buyout_price, F.data == "create:buyout:skip")
async def create_buyout_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(buyout_price=None)
    await state.set_state(AuctionCreateStates.waiting_min_step)
    await callback.answer("Выкуп пропущен")
    await _prompt_min_step(callback)


@router.message(AuctionCreateStates.waiting_buyout_price, F.text)
async def create_buyout_step(message: Message, state: FSMContext) -> None:
    buyout_price = _parse_usd_amount(message.text)
    if buyout_price is None:
        await message.answer("Некорректная цена выкупа. Введите целое число или нажмите 'Пропустить'.")
        return

    data = await state.get_data()
    start_price = int(data["start_price"])
    if buyout_price < start_price:
        await message.answer("Цена выкупа не может быть ниже начальной цены.")
        return

    await state.update_data(buyout_price=buyout_price)
    await state.set_state(AuctionCreateStates.waiting_min_step)
    await _prompt_min_step(message)


@router.message(AuctionCreateStates.waiting_buyout_price)
async def create_buyout_step_invalid(message: Message) -> None:
    await message.answer("Введите цену выкупа текстом или нажмите 'Пропустить'.")


@router.message(AuctionCreateStates.waiting_min_step, F.text)
async def create_min_step_step(message: Message, state: FSMContext) -> None:
    min_step = _parse_usd_amount(message.text)
    if min_step is None:
        await message.answer("Некорректный шаг. Введите целое число USD, минимум 1.")
        return

    await state.update_data(min_step=min_step)
    await state.set_state(AuctionCreateStates.waiting_duration)
    await message.answer("Выберите длительность аукциона.", reply_markup=duration_keyboard())


@router.message(AuctionCreateStates.waiting_min_step)
async def create_min_step_step_invalid(message: Message) -> None:
    await message.answer("Введите минимальный шаг текстом (например: 1).")


@router.callback_query(AuctionCreateStates.waiting_duration, F.data.startswith("create:duration:"))
async def create_duration_step(callback: CallbackQuery, state: FSMContext) -> None:
    duration_raw = callback.data.split(":")[-1]
    if duration_raw not in {"6", "12", "18", "24"}:
        await callback.answer("Некорректная длительность", show_alert=True)
        return

    await state.update_data(duration_hours=int(duration_raw))
    await state.set_state(AuctionCreateStates.waiting_anti_sniper)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Антиснайпер включить?\n"
            "(если ставка в последние 2 минуты, дедлайн продлится на 3 минуты, максимум 3 раза)",
            reply_markup=anti_sniper_keyboard(),
        )


@router.callback_query(AuctionCreateStates.waiting_anti_sniper, F.data.startswith("create:antisniper:"))
async def create_anti_sniper_step(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        return

    async with SessionFactory() as session:
        if await is_tg_user_blacklisted(session, callback.from_user.id):
            await callback.answer("Вы в черном списке", show_alert=True)
            await state.clear()
            return

    anti_sniper = callback.data.endswith(":1")
    await state.update_data(anti_sniper_enabled=anti_sniper)
    data = await state.get_data()

    async with SessionFactory() as session:
        publish_gate = None
        seller = await upsert_user(session, callback.from_user, mark_private_started=True)
        auction = await create_draft_auction(
            session,
            seller_user_id=seller.id,
            photo_file_id=data["photo_file_id"],
            description=data["description"],
            start_price=int(data["start_price"]),
            buyout_price=data.get("buyout_price"),
            min_step=int(data["min_step"]),
            duration_hours=int(data["duration_hours"]),
            anti_sniper_enabled=anti_sniper,
        )
        view = await load_auction_view(session, auction.id)
        publish_gate = await evaluate_seller_publish_gate(session, seller_user_id=seller.id)
        await session.commit()

    await state.clear()
    await callback.answer("Черновик создан")

    if view is None:
        await callback.message.answer("Не удалось собрать предпросмотр. Попробуйте снова.")
        return

    publish_blocked = publish_gate is not None and not publish_gate.allowed
    caption = render_auction_caption(view, publish_pending=not publish_blocked)
    await callback.message.answer_photo(
        photo=view.auction.photo_file_id,
        caption=caption,
        reply_markup=None if publish_blocked else draft_publish_keyboard(str(view.auction.id)),
    )
    if publish_blocked:
        await callback.message.answer(publish_gate.block_message or "Публикация временно ограничена")
        return

    await callback.message.answer(
        "Нажмите 'Опубликовать в чате/канале', выберите нужный чат/раздел и отправьте карточку."
    )


@router.callback_query(AuctionCreateStates.waiting_duration)
async def create_duration_invalid(callback: CallbackQuery) -> None:
    await callback.answer("Выберите одну из кнопок длительности", show_alert=True)


@router.callback_query(AuctionCreateStates.waiting_anti_sniper)
async def create_anti_sniper_invalid(callback: CallbackQuery) -> None:
    await callback.answer("Выберите: включить или выключить", show_alert=True)
