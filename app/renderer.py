from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

if TYPE_CHECKING:
    from .storage import MediaStore


logger = logging.getLogger(__name__)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


def _button_rows(state: dict[str, Any]) -> list[list[dict[str, Any]]]:
    rows = state.get("button_rows")
    if isinstance(rows, list):
        parsed: list[list[dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, list):
                continue
            parsed_row = [btn for btn in row if isinstance(btn, dict)]
            if parsed_row:
                parsed.append(parsed_row)
        if parsed:
            return parsed

    fallback = state.get("buttons")
    if isinstance(fallback, list) and fallback:
        return [[btn for btn in fallback if isinstance(btn, dict)]]
    return []


def build_markup(
    state: dict[str, Any],
    token_by_action: Callable[[str], str],
) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | None:
    rows = _button_rows(state)
    if not rows:
        return None

    types = {
        str(btn.get("type") or "")
        for row in rows
        for btn in row
        if str(btn.get("text") or "").strip()
    }

    if types == {"KeyboardButton"}:
        keyboard: list[list[KeyboardButton]] = []
        for row in rows:
            k_row: list[KeyboardButton] = []
            for btn in row:
                text = str(btn.get("text") or "").strip()
                if text:
                    k_row.append(KeyboardButton(text=text))
            if k_row:
                keyboard.append(k_row)
        if keyboard:
            return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
        return None

    inline_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        i_row: list[InlineKeyboardButton] = []
        for btn in row:
            text = str(btn.get("text") or "").strip()
            if not text:
                continue
            btn_type = str(btn.get("type") or "")
            if btn_type == "KeyboardButtonUrl":
                url = str(btn.get("url") or "").strip()
                if url:
                    i_row.append(InlineKeyboardButton(text=text, url=url))
                    continue
            i_row.append(
                InlineKeyboardButton(
                    text=text,
                    callback_data=token_by_action(text),
                )
            )
        if i_row:
            inline_rows.append(i_row)

    if inline_rows:
        return InlineKeyboardMarkup(inline_keyboard=inline_rows)
    return None


async def send_state(
    msg: Any,
    state: dict[str, Any],
    *,
    media_dir: Path,
    media_store: MediaStore,
    token_by_action: Callable[[str], str],
) -> None:
    markup = build_markup(state, token_by_action)
    html_text = str(state.get("text_html") or "")
    plain_text = str(state.get("text") or "")
    state_id = str(state.get("id") or "unknown")

    media_relpath = _media_relpath(state.get("media"))
    if not media_relpath:
        await _send_text(msg, html_text, plain_text, markup, state_id)
        return

    filename = Path(media_relpath.replace("\\", "/")).name
    media_path = media_dir / filename
    if not media_path.exists():
        await _send_text(msg, html_text, plain_text, markup, state_id)
        return

    ext = media_path.suffix.lower()
    
    # Пытаемся взять из кэша
    cached_file_id = media_store.get_file_id(filename)
    file_payload: Any = cached_file_id if cached_file_id else FSInputFile(str(media_path))
    
    sent_msg = None
    try:
        if ext in IMAGE_EXTENSIONS:
            sent_msg = await msg.answer_photo(file_payload, caption=html_text or None, reply_markup=markup)
        elif ext in VIDEO_EXTENSIONS:
            sent_msg = await msg.answer_video(file_payload, caption=html_text or None, reply_markup=markup)
        else:
            sent_msg = await msg.answer_document(file_payload, caption=html_text or None, reply_markup=markup)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            logger.error(f"Broken HTML in state {state_id}: {html_text}")
            # Фолбэк на обычный текст
            if ext in IMAGE_EXTENSIONS:
                sent_msg = await msg.answer_photo(file_payload, caption=plain_text or None, reply_markup=markup, parse_mode=None)
            elif ext in VIDEO_EXTENSIONS:
                sent_msg = await msg.answer_video(file_payload, caption=plain_text or None, reply_markup=markup, parse_mode=None)
            else:
                sent_msg = await msg.answer_document(file_payload, caption=plain_text or None, reply_markup=markup, parse_mode=None)
        else:
            raise
    except Exception as e:
        logger.error(f"Error sending media in state {state_id}: {e}")
        # Если кэшированный file_id протух (редко, но бывает), пробуем отправить файл заново
        if cached_file_id:
            logger.info(f"Retrying with direct file upload for {filename}")
            await media_store.set_file_id(filename, "") # Сброс битого кэша
            return await send_state(msg, state, media_dir=media_dir, media_store=media_store, token_by_action=token_by_action)
        raise

    # Если отправили успешно и это был новый файл - сохраняем ID
    if sent_msg and not cached_file_id:
        new_id = None
        if sent_msg.photo:
            new_id = sent_msg.photo[-1].file_id
        elif sent_msg.video:
            new_id = sent_msg.video.file_id
        elif sent_msg.document:
            new_id = sent_msg.document.file_id
        
        if new_id:
            await media_store.set_file_id(filename, new_id)


async def _send_text(
    msg: Any,
    html_text: str,
    plain_text: str,
    markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None,
    state_id: str,
) -> None:
    if not html_text and not plain_text:
        return
    
    try:
        await msg.answer(html_text or plain_text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            logger.error(f"Broken HTML in text state {state_id}: {html_text}")
            await msg.answer(plain_text or html_text, reply_markup=markup, parse_mode=None)
        else:
            raise
    except Exception as e:
        logger.error(f"Error sending text in state {state_id}: {e}")
        await msg.answer(plain_text or html_text, reply_markup=markup, parse_mode=None)


def _media_relpath(media: Any) -> str:
    if isinstance(media, str):
        return media.strip()
    if isinstance(media, dict):
        value = str(media.get("relpath") or "").strip()
        if value:
            return value
    return ""
