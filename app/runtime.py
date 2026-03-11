from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, FSInputFile, Message
from dotenv import dotenv_values, load_dotenv

from .catalog import (
    FLOW_CATALOG_RE_MAP,
    FlowCatalog,
)
from .constants import (
    DEFAULT_LINKS,
    PAYMENT_PROOF_NEED_PHOTO,
    PAYMENT_PROOF_PROMPT,
    PAYMENT_PROOF_SENT,
    PAYMENT_PROOF_STORED,
)
from .context import AppContext
from .handlers.admin import build_admin_router
from .overrides import RuntimeOverrides, apply_state_overrides
from .payment import OrderExtractor, PaymentHandler
from .rates import RateService
from .renderer import send_state
from .sessions import UserSession
from .storage import MediaStore, OrdersStore, SessionData, SessionsStore, SettingsStore, UsersStore
from .tokens import TokenRegistry
from .utils import (
    is_valid_crypto_address,
    parse_admin_ids,
    parse_non_negative_amount,
)

logger = logging.getLogger(__name__)

# Configure basic logging if not already done
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def state_button_rows(state: dict[str, Any]) -> list[list[dict[str, Any]]]:
    rows = state.get("button_rows")
    parsed: list[list[dict[str, Any]]] = []
    if isinstance(rows, list):
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


class FlowRuntime:
    _MAX_AMOUNT_RUB_BUDGET = 1_000_000.0
    _MIN_AMOUNT_RUB_BUDGET = 1_500.0
    _AMOUNT_DUST_FLOOR = 0.00001
    _MAX_AMOUNT_ERROR_STATE_IDS = {
        "2fed3c394a37b41f55f21d474b5734ae",  # BTC max error
        "962eb30dd5a37037bc9d0c643dc390b8",  # XMR max error
    }
    _MIN_AMOUNT_STATE_IDS = {
        "4638c2dc946f913813ff1d81427e5703",
        "dd8e48ace94f57bf3eba334f6ab5b7d2",
        "d10355801a11f2d98b2f14663355934e",
        "c7dc1b492541b449585da857e71c7e29",  # XMR amount
    }

    def __init__(
        self,
        *,
        project_dir: Path,
        catalog: FlowCatalog,
        app_context: AppContext,
    ):
        self.project_dir = project_dir
        self.raw_dir = project_dir / "data" / "raw"
        self.media_dir = project_dir / "data" / "media"
        self.catalog = catalog
        self.app_context = app_context
        
        self.sessions: dict[int, UserSession] = {}
        self.tokens = TokenRegistry()
        self.payment = PaymentHandler(app_context, project_dir)
        self.global_actions: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task] = set()
        
        self._warmup_tokens()
        self._discover_global_actions()
        self._load_persisted_sessions()

    def _load_persisted_sessions(self) -> None:
        """Load sessions from disk."""
        count = 0
        for uid_str, data in self.app_context.sessions.data.items():
            try:
                self.sessions[int(uid_str)] = UserSession.from_dict(cast(dict[str, Any], data))
                count += 1
            except Exception as e:
                logger.error(f"Failed to load session for user {uid_str}: {e}")
                continue
        logger.info(f"Loaded {count} sessions from disk.")

    async def save_sessions(self) -> None:
        """Persist only changed active sessions to disk."""
        dirty_count = 0
        for uid, session in self.sessions.items():
            if session._dirty:
                session_data = cast(SessionData, session.to_dict())
                self.app_context.sessions.update_session(uid, session_data)
                session.clear_dirty()
                dirty_count += 1
        
        if dirty_count > 0:
            logger.info(f"Saving {dirty_count} changed sessions...")
            await self.app_context.sessions.save()

    async def run_loops(self) -> None:
        """Start background tasks for session persistence and cleanup."""
        p_task = asyncio.create_task(self._persistence_loop())
        c_task = asyncio.create_task(self._cleanup_loop())
        self._background_tasks.add(p_task)
        self._background_tasks.add(c_task)
        p_task.add_done_callback(self._background_tasks.discard)
        c_task.add_done_callback(self._background_tasks.discard)

    async def stop(self) -> None:
        """Gracefully stop the runtime and save state."""
        logger.info("Stopping FlowRuntime...")
        # Копируем сет, так как discard меняет его во время итерации
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        await self.save_sessions()
        # Также синхронно сохраняем медиа кэш и настройки на всякий случай
        self.app_context.media.save_sync()
        self.app_context.settings.save_sync()
        logger.info("FlowRuntime stopped.")

    async def _persistence_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.save_sessions()
            except Exception as e:
                logger.error(f"Error in persistence loop: {e}")
                continue

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)  # Every hour
            try:
                # Cleanup sessions in memory and on disk older than 24h
                now = time.time()
                max_age = 86400
                to_delete = [uid for uid, sess in self.sessions.items() if now - sess.updated_at > max_age]
                for uid in to_delete:
                    del self.sessions[uid]
                
                deleted_count = await self.app_context.sessions.cleanup(max_age)
                if to_delete or deleted_count:
                    logger.info(f"Cleaned up {len(to_delete)} in-memory and {deleted_count} on-disk sessions.")
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                continue

    def _warmup_tokens(self) -> None:
        """Pre-populate token_actions so buttons work after restart."""
        for state in self.catalog.states.values():
            rows = state.get("button_rows") or []
            if not rows and state.get("buttons"):
                rows = [state.get("buttons")]
            for row in rows:
                if not isinstance(row, list):
                    continue
                for btn in row:
                    if not isinstance(btn, dict):
                        continue
                    text = str(btn.get("text") or "").strip()
                    if text and btn.get("type") != "KeyboardButtonUrl":
                        self.tokens.get_token(text)

    def _discover_global_actions(self) -> None:
        """Identify main menu buttons that should work from any state."""
        start_state = self.catalog.states.get(self.catalog.start_state_id)
        if not start_state:
            return
        
        # We look at buttons in the start state
        rows = state_button_rows(start_state)
        for row in rows:
            for btn in row:
                text = str(btn.get("text") or "").strip()
                if not text:
                    continue
                target = self.catalog.resolve_action(self.catalog.start_state_id, text)
                if target:
                    self._register_global_action(text, target)

    def _register_global_action(self, text: str, target: str) -> None:
        self.global_actions[text] = target
        # Also register variants without emoji or with normal spaces
        clean = re.sub(r"[^\w\s]", "", text).strip()
        if clean and clean != text:
            self.global_actions[clean] = target
        
        # Variant: just the text without trailing/leading non-alphanumeric
        trimmed = text.strip(" 🔙🔄👤⚙️📈🆘✅❌")
        if trimmed and trimmed != text and trimmed != clean:
            self.global_actions[trimmed] = target

    async def start(self, msg: Message) -> None:
        user = msg.from_user
        if user is None:
            return
        user_id = int(user.id)
        start_sid = self.catalog.start_state_id
        session = UserSession(state_id=start_sid, history=[start_sid])
        self.sessions[user_id] = session
        await self._send_state_by_id(msg, start_sid, session=session)

    async def on_callback(self, cb: CallbackQuery) -> None:
        user = cb.from_user
        if user is None:
            await cb.answer()
            return

        user_id = int(user.id)
        session = self.sessions.get(user_id)
        
        # Anti-Spam
        now = time.time()
        if session and now - session.last_action_ts < 0.4:
            await cb.answer("⚠️ Слишком часто!", show_alert=False)
            return
        if session:
            session.last_action_ts = now

        callback_message = cb.message if isinstance(cb.message, Message) else None
        token = str(cb.data or "")
        action_text = self.tokens.get_action(token)
        if not action_text:
            action_text = self._extract_action_text_from_callback(callback_message, token)
            if action_text:
                self.tokens.token_to_action[token] = action_text
                self.tokens.action_to_token[action_text] = token
        if not action_text:
            await cb.answer()
            return

        session = self.sessions.get(int(user.id))
        if session is None:
            if callback_message is not None:
                await self.start(callback_message)
            await cb.answer()
            return

        # 1. Global Menu Actions
        global_target = self.global_actions.get(action_text)
        if global_target and callback_message is not None:
            # We jump but keep history unless it's the start state
            reset = (global_target == self.catalog.start_state_id)
            session.jump_to_state(global_target, reset_history=reset)
            session.awaiting_payment_proof = False
            await self._send_state_by_id(callback_message, global_target, session=session)
            await cb.answer()
            return

        # 2. Back Button
        if self._is_back_action(action_text):
            prev_state = self._resolve_back_state(session, action_text)
            if prev_state and callback_message is not None:
                await self._send_state_by_id(callback_message, prev_state, session=session)
                await cb.answer()
                return

        selected_method = self._match_payment_method(action_text)
        if selected_method:
            session.selected_payment_method = selected_method
        selected_coin = self._extract_coin_symbol(action_text)
        if selected_coin:
            session.selected_coin = selected_coin

        if action_text == "✅ Я оплатил" and callback_message is not None:
            session.awaiting_payment_proof = True
            session.payment_context = self._state_text(session.state_id)
            await callback_message.answer(PAYMENT_PROOF_PROMPT)
            await cb.answer()
            return

        next_state = self._resolve_contextual_transition(session.state_id, action_text, session)
        if not next_state:
            next_state = self.catalog.resolve_action(session.state_id, action_text)
        if not next_state:
            next_state = self._resolve_missing_action_transition(session.state_id, action_text)
        if next_state and callback_message is not None:
            session.push_state(next_state)
            session.awaiting_payment_proof = False
            await self._send_state_by_id(callback_message, next_state, session=session)
            await self._send_system_chain(callback_message, session)

        await cb.answer()

    async def on_message(self, msg: Message) -> None:
        user = msg.from_user
        if user is None:
            return
        photos = list(getattr(msg, "photo", None) or [])

        # Handle photos if awaiting payment proof
        user_id = int(user.id)
        session = self.sessions.get(user_id)
        
        # Anti-Spam
        now = time.time()
        if session and now - session.last_action_ts < 0.4:
            return
        if session:
            session.last_action_ts = now

        if session is not None and session.awaiting_payment_proof:
            if photos:
                await self._handle_payment_proof(msg, session)
                return

        text = str(msg.text or msg.caption or "").strip()
        if text.startswith("/"):
            if text == "/start" and session:
                session.awaiting_payment_proof = False
            return

        if session is None:
            await self.start(msg)
            return

        if self._expects_photo_input(session.state_id):
            if photos:
                await self._handle_verification_photo(msg, session)
                return
            if text:
                await msg.answer(self._input_error_message(session.state_id, session=session))
                return
        
        session.last_input = text
        session.mark_dirty()

        # 1. Global Menu Actions
        global_target = self.global_actions.get(text)
        if not global_target and text:
            # Try fuzzy match for globals (ignore emoji)
            clean_text = re.sub(r"[^\w\s]", "", text).strip()
            global_target = self.global_actions.get(clean_text)

        if global_target:
            reset = (global_target == self.catalog.start_state_id)
            session.jump_to_state(global_target, reset_history=reset)
            session.awaiting_payment_proof = False
            await self._send_state_by_id(msg, global_target, session=session)
            return

        # 2. Back/Cancel Button
        if self._is_back_action(text):
            prev_state = self._resolve_back_state(session, text)
            if prev_state:
                await self._send_state_by_id(msg, prev_state, session=session)
                return

        selected_method = self._match_payment_method(text)
        if selected_method:
            session.selected_payment_method = selected_method
        selected_coin = self._extract_coin_symbol(text)
        if selected_coin:
            session.selected_coin = selected_coin

        if text == "✅ Я оплатил":
            session.awaiting_payment_proof = True
            session.payment_context = self._state_text(session.state_id)
            await msg.answer(PAYMENT_PROOF_PROMPT)
            return

        if text and await self._handle_max_amount_retry(msg, session, text):
            return

        expected_input_kind = self._expected_input_kind(session.state_id, session=session)
        if text and expected_input_kind in {"card", "amount", "address"}:
            if not self._validate_input(session.state_id, text, session=session):
                await msg.answer(self._input_error_message(session.state_id, session=session))
                return

        # 3. Resolve Transition
        next_state = self._resolve_contextual_transition(session.state_id, text, session)
        if not next_state:
            next_state = self.catalog.resolve_action(session.state_id, text)
        if not next_state:
            next_state = self._resolve_missing_action_transition(session.state_id, text)
        
        # If no explicit action, but state accepts input, we validate and use <manual-input>/<input>
        if next_state is None and self.catalog.state_accepts_input(session.state_id):
            if not text and not photos:
                await msg.answer(self._input_error_message(session.state_id, session=session))
                return

            if text and not self._validate_input(session.state_id, text, session=session):
                await msg.answer(self._input_error_message(session.state_id, session=session))
                return

            # If it's a photo, we also allow transition if text is empty
            if text or photos:
                if photos and not session.awaiting_payment_proof:
                    # Forward non-payment photo to admins as 'general input'
                    await self._forward_general_photo(msg, session)
                
                next_state = self.catalog.resolve_action(session.state_id, text, is_text_input=True)

        if (
            next_state is None
            and text
            and self._state_has_only_system_next(session.state_id)
            and self._state_explicitly_requests_text_input(session.state_id)
        ):
            next_state = self.catalog.resolve_system_next(session.state_id)

        if not next_state:
            # If no transition found, check if this text might be a known button in start state 
            # (sometimes users type buttons that are not in current state but are 'global' in their mind)
            return

        session.push_state(next_state)
        session.awaiting_payment_proof = False
        await self._send_state_by_id(msg, next_state, session=session)
        await self._send_system_chain(msg, session)

    async def _handle_verification_photo(self, msg: Message, session: UserSession) -> None:
        """Step 1 — immediately acknowledge; Step 2 — after 15s send success."""
        import re as _re
        state_text = self._state_text(session.state_id)
        card_match = _re.search(r"\b\d{4}(?:[ \-]?\d{4}){3}\b", state_text)
        card_line = f"\n\n📋 Карта: <code>{card_match.group(0)}</code>" if card_match else ""

        accept_text = (
            f"✅ <b>Заявка на верификацию принята!</b>"
            f"{card_line}"
            f"\n\n⏳ Ваша заявка будет рассмотрена в ближайшее время. "
            f"Вы получите уведомление о результате."
        )

        naproverk_path = self.media_dir / "naproverk.jpg"
        if naproverk_path.exists():
            try:
                await msg.answer_photo(
                    photo=FSInputFile(str(naproverk_path)),
                    caption=accept_text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning(f"Failed to send naproverk media: {e}")
                await msg.answer(accept_text, parse_mode=ParseMode.HTML)
        else:
            await msg.answer(accept_text, parse_mode=ParseMode.HTML)

        await asyncio.sleep(15)
        await self._send_verification_success(msg)

    async def _send_verification_success(self, msg: Message) -> None:
        caption = "✅ <b>Успешная верификация!</b>"
        media_path = self.media_dir / "verif.png"
        if media_path.exists():
            try:
                await msg.answer_photo(
                    photo=FSInputFile(str(media_path)),
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception as e:
                logger.warning(f"Failed to send verification success media: {e}")
        await msg.answer(caption, parse_mode=ParseMode.HTML)

    async def _handle_max_amount_retry(self, msg: Message, session: UserSession, text: str) -> bool:
        if session.state_id not in self._MAX_AMOUNT_ERROR_STATE_IDS:
            return False
        parsed = self._parse_amount_text(text)
        if parsed is None:
            await msg.answer("⚠️ Введите корректную сумму числом.")
            return True
        coin = (session.selected_coin or "BTC").upper()
        live_max = await self._coin_max_amount(coin)
        # Use the higher of the live rate and what was displayed to the user —
        # prevents an infinite loop when the rate drifts slightly between render and input.
        max_allowed = max(live_max, session.last_shown_max)
        if parsed > max_allowed:
            await self._send_state_by_id(msg, session.state_id, session=session)
            return True
        next_state = self.catalog.resolve_system_next(session.state_id)
        if not next_state:
            return True
        session.push_state(next_state)
        session.awaiting_payment_proof = False
        await self._send_state_by_id(msg, next_state, session=session)
        await self._send_system_chain(msg, session)
        return True

    def _parse_amount_text(self, text: str) -> float | None:
        normalized = (text or "").strip().replace(" ", "").replace(",", ".")
        if not normalized:
            return None
        try:
            value = float(normalized)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value

    async def _coin_max_amount(self, coin: str) -> float:
        symbol = (coin or "BTC").upper()
        if symbol == "USDT":
            rates = await self._get_live_rates_rub()
            usdt_rate = float(rates.get("USDT") or 0.0)
            if usdt_rate > 0:
                return self._MAX_AMOUNT_RUB_BUDGET / usdt_rate
            return self._MAX_AMOUNT_RUB_BUDGET
        rates = await self._get_live_rates_rub()
        coin_rate = float(rates.get(symbol) or 0.0)
        if coin_rate > 0:
            return self._MAX_AMOUNT_RUB_BUDGET / coin_rate
        return self._MAX_AMOUNT_RUB_BUDGET

    async def _coin_min_amount(self, coin: str) -> float | None:
        symbol = (coin or "BTC").upper()
        if symbol == "USDT":
            rates = await self._get_live_rates_rub()
            usdt_rate = float(rates.get("USDT") or 0.0)
            if usdt_rate > 0:
                return self._MIN_AMOUNT_RUB_BUDGET / usdt_rate
            return None
        rates = await self._get_live_rates_rub()
        coin_rate = float(rates.get(symbol) or 0.0)
        if coin_rate > 0:
            return self._MIN_AMOUNT_RUB_BUDGET / coin_rate
        return None

    def _resolve_back_state(self, session: UserSession, action_text: str) -> str | None:
        """Try to find an explicit 'Back' edge, otherwise pop history."""
        normalized_action = self._normalize_action_text(action_text)
        is_cancel_action = ("отмена" in normalized_action) or (normalized_action == "cancel")
        if is_cancel_action and self._is_verification_state(session.state_id):
            # In verification flow, "Cancel" should exit the flow, not advance deeper.
            while True:
                prev_state = session.pop_state()
                if prev_state is None:
                    break
                if not self._is_verification_state(prev_state):
                    return prev_state
            start_sid = self.catalog.start_state_id
            if session.state_id != start_sid:
                session.jump_to_state(start_sid, reset_history=True)
                return start_sid
            return None

        explicit = self.catalog.resolve_action(session.state_id, action_text)
        if explicit:
            session.push_state(explicit)
            return explicit
        
        # Pop from history
        prev_state = session.pop_state()
        if prev_state is None:
            # If stack is empty or has only 1 element left, send user to start state
            start_sid = self.catalog.start_state_id
            if session.state_id != start_sid:
                session.jump_to_state(start_sid, reset_history=True)
                return start_sid
            return None # Already at start state, do nothing

        return prev_state

    def _validate_input(self, state_id: str, text: str, *, session: UserSession | None = None) -> bool:
        """Validate text input based on the current state's prompt."""
        normalized_text = (text or "").strip()
        expected_kind = self._expected_input_kind(state_id, session=session)
        state_text = self._state_text(state_id).upper()

        if expected_kind == "card":
            digits_only = re.sub(r"\D", "", normalized_text)
            return len(digits_only) == 16 and digits_only.isdigit()

        if expected_kind == "address":
            for coin in FLOW_CATALOG_RE_MAP:
                if coin in state_text and (
                    "АДРЕС" in state_text or "WALLET" in state_text or "КОШЕЛЕК" in state_text or "ПРИСЛАТЬ" in state_text
                ):
                    return is_valid_crypto_address(normalized_text, coin, network_hint=state_text)

            if len(normalized_text) > 20 and " " not in normalized_text:
                if is_valid_crypto_address(normalized_text, "BTC"):
                    return True
                if is_valid_crypto_address(normalized_text, "TRX"):
                    return True
                if is_valid_crypto_address(normalized_text, "ETH"):
                    return True
                if is_valid_crypto_address(normalized_text, "USDT", network_hint=state_text):
                    return True
            return False

        if expected_kind == "amount":
            try:
                val_str = normalized_text.replace(",", ".").replace(" ", "")
                val = float(val_str)
            except ValueError:
                return False
            coin = ((session.selected_coin if session else "") or self._extract_coin_from_state_text(state_text) or "").upper()
            if coin and coin != "RUB" and val < self._AMOUNT_DUST_FLOOR:
                return False
            return val > 0

        if expected_kind == "photo":
            return False

        return bool(normalized_text)

    def _expected_input_kind(self, state_id: str, *, session: UserSession | None = None) -> str:
        state_text = self._state_text(state_id).upper()

        if self._expects_photo_input(state_id):
            return "photo"

        if "16 ЦИФР" in state_text and "КАРТ" in state_text:
            return "card"
        if "НОМЕР ВАШЕЙ БАНКОВСКОЙ КАРТЫ" in state_text:
            return "card"

        if (
            "АДРЕС" in state_text
            or "WALLET" in state_text
            or "КОШЕЛЕК" in state_text
            or "КОШЕЛЁК" in state_text
        ):
            return "address"

        if "СУММ" in state_text or "AMOUNT" in state_text:
            return "amount"

        if session is not None and self.catalog.state_accepts_input(state_id):
            return "text"
        return "unknown"

    def _input_error_message(self, state_id: str, *, session: UserSession | None = None) -> str:
        expected_kind = self._expected_input_kind(state_id, session=session)
        if expected_kind == "card":
            return "⚠️ Введите корректный номер карты: 16 цифр, можно с пробелами или без."
        if expected_kind == "amount":
            return "⚠️ Введите корректную сумму числом."
        if expected_kind == "address":
            return "⚠️ Введите корректный адрес кошелька."
        if expected_kind == "photo":
            return "⚠️ На этом шаге нужно отправить именно фото карты с листком и паролем."
        return "⚠️ Введенные данные некорректны. Пожалуйста, проверьте формат и попробуйте снова."

    def _expects_photo_input(self, state_id: str) -> bool:
        return self._is_verification_photo_state(state_id)

    def _find_error_state(self, state_id: str, text: str) -> str | None:
        """Try to find a state in edges that looks like an error state for this input."""
        action_map = self.catalog.transition_index.get(state_id) or {}
        
        # 1. Look for explicit error actions like '<invalid-input>' if they exist (rare in capture)
        # 2. Look for edges from this state that lead to 
        # states containing "некорректный", "ошибка", "попробуйте снова", "введите верно"
        
        # We check all possible next states from here
        all_targets: set[str] = set()
        for targets in action_map.values():
            all_targets.update(targets)
            
        for target in all_targets:
            target_text = self._state_text(target).lower()
            if any(hint in target_text for hint in ("некорректный", "ошибка", "попробуйте снова", "введите верно", "неверный")):
                # Check if this error state is specific to the coin mentioned in current state
                state_text_lower = self._state_text(state_id).lower()
                if "usdt" in state_text_lower and "usdt" in target_text:
                    return target
                if "btc" in state_text_lower and "btc" in target_text:
                    return target
                # If no coin-specific error, return the first error found
                return target
                
        return None

    async def _forward_general_photo(self, msg: Message, session: UserSession) -> None:
        """Forward a photo that isn't a payment proof to admins (e.g. for verification)."""
        photos = list(msg.photo or [])
        if not photos or msg.bot is None:
            return
        user = msg.from_user
        if not user:
            return
        
        photo_file_id = photos[-1].file_id
        caption = self.payment.build_admin_caption(
            order_id="INPUT_PHOTO",
            user_id=int(user.id),
            username=(user.username or ""),
            order_context=f"Пользователь отправил фото в состоянии: {session.state_id}\nТекст состояния: {self._state_text(session.state_id)[:500]}"
        )
        
        await self.payment.forward_to_admins(
            bot=msg.bot,
            photo_file_id=photo_file_id,
            caption=caption,
            order_id="N/A"
        )

    async def _handle_payment_proof(self, msg: Message, session: UserSession) -> None:
        photos = list(msg.photo or [])
        if not photos:
            text = (msg.text or "").strip().lower()
            if text in ("назад", "отмена", "cancel", "back", "❌ отмена", "🔙 назад"):
                session.awaiting_payment_proof = False
                session.mark_dirty()
                await msg.answer("❌ Загрузка фото отменена.")
                # Переотправляем текущее состояние, чтобы юзер видел кнопки
                await self._send_state_by_id(msg, session.state_id, session=session)
                return
            
            await msg.answer(PAYMENT_PROOF_NEED_PHOTO)
            return

        user = msg.from_user
        if user is None or msg.bot is None:
            return

        photo_file_id = photos[-1].file_id
        order = await self._create_paid_order(user_id=int(user.id), username=(user.username or ""), session=session)
        order_id = order["order_id"]

        caption = self.payment.build_admin_caption(
            order_id=order_id,
            user_id=int(user.id),
            username=(user.username or ""),
            order_context=session.payment_context,
        )

        forwarded = await self.payment.forward_to_admins(
            bot=msg.bot,
            photo_file_id=photo_file_id,
            caption=caption,
            order_id=order_id,
        )

        self.payment.store_payment_proof(
            user_id=int(user.id),
            username=(user.username or ""),
            order_id=order_id,
            order_context=session.payment_context,
            photo_file_id=photo_file_id,
            forwarded_to_admins=forwarded,
        )

        session.awaiting_payment_proof = False
        session.payment_context = ""

        if forwarded:
            await msg.answer(PAYMENT_PROOF_SENT)
        else:
            await msg.answer(PAYMENT_PROOF_STORED)

    async def _create_paid_order(self, *, user_id: int, username: str, session: UserSession) -> Any:
        details = OrderExtractor.extract_details(session.payment_context)
        if session.selected_coin:
            details["coin_symbol"] = session.selected_coin

        payment_method = session.selected_payment_method or self._default_payment_method()
        bank = self._effective_bank_for_session(session, session.state_id)
        order = await self.app_context.orders.create_order(
            user_id=user_id,
            username=username,
            wallet=details["wallet"],
            coin_symbol=details["coin_symbol"],
            coin_amount=details["coin_amount"],
            amount_rub=details["amount_rub"],
            payment_method=payment_method,
            bank=bank,
        )
        await self.app_context.orders.mark_paid(order["order_id"])
        return order

    async def _send_state_by_id(self, msg: Message, state_id: str, *, session: UserSession | None) -> None:
        base_state = self.catalog.states.get(state_id)
        if not base_state:
            return
        if self._is_requisites_order_state(base_state):
            await self._send_requisites_selection_notice(msg)
            await asyncio.sleep(15)

        overrides = RuntimeOverrides(
            operator_url=self.app_context.settings.link("operator"),
            payment_requisites=self._effective_requisites_for_state(session, state_id),
            link_overrides=self.app_context.settings.all_links(),
            sell_wallet_overrides=self.app_context.settings.all_sell_wallets(),
            commission_percent=self.app_context.settings.commission_percent,
        )
        live_rates_rub = await self._get_live_rates_rub()
        state = apply_state_overrides(
            state=base_state,
            overrides=overrides,
            operator_url_aliases=self.catalog.operator_url_aliases,
            operator_handle_aliases=self.catalog.operator_handle_aliases,
            detected_requisites=self.catalog.detected_requisites,
            link_url_aliases=self.catalog.link_url_aliases,
            sell_wallet_aliases=self.catalog.sell_wallet_aliases,
            live_rates_rub=live_rates_rub,
        )
        state = self._apply_selected_coin_theming(state, state_id=state_id, session=session)
        state = await self._apply_dynamic_amount_limits(
            state,
            state_id=state_id,
            session=session,
        )

        await send_state(
            msg,
            state,
            media_dir=self.media_dir,
            media_store=self.app_context.media,
            token_by_action=self.tokens.get_token,
        )

    async def _send_requisites_selection_notice(self, msg: Message) -> None:
        caption = "⏳ <b>Подбор реквизитов, 15 сек...</b>"
        media_path = self.media_dir / "requisites_wait.png"
        if media_path.exists():
            try:
                await msg.answer_photo(
                    photo=FSInputFile(str(media_path)),
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception as e:
                logger.warning(f"Failed to send requisites wait media: {e}")
        await msg.answer(caption, parse_mode=ParseMode.HTML)

    async def _send_system_chain(self, msg: Message, session: UserSession, max_hops: int = 4) -> None:
        seen: set[str] = {session.state_id}
        current = session.state_id
        hops = 0

        while hops < max_hops:
            if self.catalog.state_has_buttons(current):
                break
            next_state = self.catalog.resolve_system_next(current)
            if not next_state or next_state in seen:
                break
            seen.add(next_state)
            session.state_id = next_state
            session.history.append(next_state)
            await self._send_state_by_id(msg, next_state, session=session)
            current = next_state
            hops += 1

    def _state_text(self, state_id: str) -> str:
        state = self.catalog.states.get(state_id) or {}
        return str(state.get("text") or "")

    def _is_requisites_order_state(self, state: dict[str, Any]) -> bool:
        text = "\n".join(
            [
                str(state.get("text") or ""),
                str(state.get("text_html") or ""),
                str(state.get("text_markdown") or ""),
            ]
        ).lower()
        return "заявка:" in text and "перевод на" in text and "номер карты" in text and "сумма:" in text

    def _is_verification_state(self, state_id: str) -> bool:
        text = self._state_text(state_id).lower()
        markers = (
            "верификац",
            "вашей банковской карты",
            "отправьте фото",
            "секретный пароль",
            "карта:",
        )
        return any(marker in text for marker in markers)

    def _is_verification_photo_state(self, state_id: str) -> bool:
        text = self._state_text(state_id).lower()
        return "теперь отправьте фото" in text and "секретный пароль" in text

    def _normalize_action_text(self, text: str) -> str:
        cleaned = (text or "").replace("\u00a0", " ").strip().lower()
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"[^\w\s()₽]", "", cleaned)
        return cleaned

    def _is_back_action(self, action_text: str) -> bool:
        action = self._normalize_action_text(action_text)
        if action in {"🔙", "назад", "back", "❌ отмена", "отмена", "cancel"}:
            return True
        return "назад" in action or "отмена" in action or action == "back" or action == "cancel"

    def _extract_action_text_from_callback(self, msg: Message | None, token: str) -> str | None:
        if msg is None or msg.reply_markup is None:
            return None
        inline = getattr(msg.reply_markup, "inline_keyboard", None)
        if not inline:
            return None
        for row in inline:
            for btn in row:
                if getattr(btn, "callback_data", None) == token:
                    return str(getattr(btn, "text", "") or "").strip() or None
        return None

    def _resolve_missing_action_transition(self, state_id: str, action_text: str) -> str | None:
        action_map = self.catalog.transition_index.get(state_id) or {}
        if not action_map:
            return None

        normalized = self._normalize_action_text(action_text)
        for key in action_map:
            if self._normalize_action_text(key) == normalized:
                targets = action_map[key]
                if targets:
                    return self.catalog._pick_target(state_id, key, targets)

        coin_target = self._resolve_missing_coin_transition(state_id, action_text)
        if coin_target:
            return coin_target

        explicit_targets: list[str] = []
        for key, targets in action_map.items():
            if key.startswith("<") and key.endswith(">"):
                continue
            explicit_targets.extend(targets)
        if explicit_targets:
            unique_targets = list(dict.fromkeys(explicit_targets))
            if len(unique_targets) == 1:
                return unique_targets[0]

        system_targets = action_map.get("<next-message>") or []
        if system_targets:
            action_words = {w for w in normalized.split() if len(w) > 2}
            best_target: str | None = None
            best_score = 0
            for target in system_targets:
                target_words = {
                    w for w in self._normalize_action_text(self._state_text(target)).split() if len(w) > 2
                }
                score = len(action_words & target_words)
                if score > best_score:
                    best_score = score
                    best_target = target
            if best_target and best_score > 0:
                return best_target

            if "понятно" in normalized or "перейти" in normalized or "без промокода" in normalized:
                return self.catalog.resolve_system_next(state_id)
        return None

    def _extract_coin_symbol(self, action_text: str) -> str:
        action = self._normalize_action_text(action_text)
        if "tether" in action or "usdt" in action:
            return "USDT"
        if "bitcoin" in action:
            return "BTC"
        if "litecoin" in action:
            return "LTC"
        if "ethereum" in action:
            return "ETH"
        if "monero" in action:
            return "XMR"

        m = re.search(r"\(([^)]+)\)\s*$", (action_text or "").strip())
        if m:
            symbol = m.group(1).strip().upper()
            if symbol == "₽":
                return "RUB"
            if symbol in {"TRC20", "BSC20"}:
                return "USDT"
            if symbol in {"BTC", "LTC", "USDT", "ETH", "XMR", "TRX", "TON", "RUB"}:
                return symbol
            return ""
        return ""

    def _resolve_contextual_transition(self, state_id: str, action_text: str, session: UserSession) -> str | None:
        action_map = self.catalog.transition_index.get(state_id) or {}
        action = (action_text or "").strip()
        targets = action_map.get(action) or []
        if len(targets) <= 1:
            return None

        coin = (session.selected_coin or "").upper()
        if not coin:
            return None

        if action == "💳 Карты на карту":
            # XMR has dedicated states — route directly instead of using BTC-themed path.
            if coin == "XMR":
                _XMR_AMOUNT = "c7dc1b492541b449585da857e71c7e29"
                # Prefer edge-registered target if present
                for target in targets:
                    target_text = self._normalize_action_text(self._state_text(target))
                    if "monero" in target_text or "xmr" in target_text:
                        return target
                if _XMR_AMOUNT in self.catalog.states:
                    return _XMR_AMOUNT

            # All other crypto coins follow BTC textual flow; only labels/media differ upstream.
            btc_target = None
            for target in targets:
                target_text = self._normalize_action_text(self._state_text(target))
                if "bitcoin (btc)" in target_text:
                    btc_target = target
                    break
            if btc_target and coin in {"BTC", "LTC", "USDT", "ETH", "TRX", "TON"}:
                return btc_target

        keywords_map: dict[str, tuple[str, ...]] = {
            "BTC": ("bitcoin", "btc"),
            "LTC": ("litecoin", "ltc"),
            "USDT": ("usdt", "tether", "trc20", "bsc20", "сеть"),
            "ETH": ("ethereum", "eth"),
            "XMR": ("monero", "xmr"),
            "RUB": ("рубль", "rub", "₽"),
        }
        keywords = keywords_map.get(coin, ())
        if keywords:
            for target in targets:
                target_text = self._normalize_action_text(self._state_text(target))
                if any(k in target_text for k in keywords):
                    return target

        if coin != "USDT":
            for target in targets:
                target_text = self._normalize_action_text(self._state_text(target))
                if not any(k in target_text for k in ("usdt", "tether", "trc20", "bsc20", "сеть")):
                    return target

        return targets[0]

    def _resolve_missing_coin_transition(self, state_id: str, action_text: str) -> str | None:
        action = (action_text or "").strip()
        if not re.search(r"\([^)]+\)\s*$", action):
            return None

        action_map = self.catalog.transition_index.get(state_id) or {}
        if not action_map:
            return None

        symbol_match = re.search(r"\(([^)]+)\)\s*$", action)
        symbol = (symbol_match.group(1).strip().upper() if symbol_match else "")
        is_rub = symbol in {"₽", "RUB"}

        target_counts: Counter[str] = Counter()
        for candidate_action, targets in action_map.items():
            if not re.search(r"\([^)]+\)\s*$", candidate_action):
                continue
            candidate_symbol_match = re.search(r"\(([^)]+)\)\s*$", candidate_action)
            candidate_symbol = (
                candidate_symbol_match.group(1).strip().upper() if candidate_symbol_match else ""
            )
            candidate_is_rub = candidate_symbol in {"₽", "RUB"}
            if candidate_is_rub != is_rub:
                continue
            for target in targets:
                target_counts[target] += 1

        if not target_counts:
            return None
        return target_counts.most_common(1)[0][0]

    def _apply_selected_coin_theming(
        self, state: dict[str, Any], *, state_id: str, session: UserSession | None
    ) -> dict[str, Any]:
        if not session or not session.selected_coin:
            return state
        coin = session.selected_coin.upper()
        if coin in {"BTC", "XMR"}:
            return state  # XMR has dedicated states, no BTC-theming needed

        # The product flow is BTC-first; adapt coin wording/media for other coins.
        themed_state_ids = {
            "dd8e48ace94f57bf3eba334f6ab5b7d2",  # amount
            "2fed3c394a37b41f55f21d474b5734ae",  # amount max error
            "dfff19cf359e360e6644c920d8eb7c6b",  # wallet
        }
        if state_id not in themed_state_ids:
            return state

        label_map = {
            "LTC": "Litecoin (LTC)",
            "USDT": "USDT ($)",
            "ETH": "Ethereum (ETH)",
            "XMR": "Monero (XMR)",
            "TRX": "TRON (TRX)",
            "TON": "TON (TON)",
        }
        replacement_label = label_map.get(coin, f"{coin} ({coin})")

        themed = dict(state)
        for key in ("text", "text_html", "text_markdown"):
            val = str(themed.get(key) or "")
            if not val:
                continue
            val = val.replace("Bitcoin (BTC)", replacement_label)
            val = re.sub(r"\bBTC\b", coin, val)
            themed[key] = val

        media_aliases = {
            "dd8e48ace94f57bf3eba334f6ab5b7d2": "amount",
            "dfff19cf359e360e6644c920d8eb7c6b": "wallet",
        }
        media_role = media_aliases.get(state_id, "")
        if media_role:
            themed_media = self._coin_media_relpath(coin=coin, role=media_role)
            if themed_media:
                themed["media"] = themed_media
        return themed

    async def _apply_dynamic_amount_limits(
        self,
        state: dict[str, Any],
        *,
        state_id: str,
        session: UserSession | None,
    ) -> dict[str, Any]:
        coin = ((session.selected_coin if session else "") or "BTC").upper()
        max_amount = await self._coin_max_amount(coin)
        min_amount = await self._coin_min_amount(coin)
        formatted_max = self._format_dynamic_limit(max_amount, coin=coin)
        formatted_min = self._format_dynamic_limit(min_amount, coin=coin) if min_amount is not None else ""

        if session is not None and state_id in self._MAX_AMOUNT_ERROR_STATE_IDS:
            session.last_shown_max = max_amount
            session.mark_dirty()

        themed = dict(state)
        for key in ("text", "text_html", "text_markdown"):
            val = str(themed.get(key) or "")
            if not val:
                continue
            if state_id in self._MAX_AMOUNT_ERROR_STATE_IDS:
                val = re.sub(
                    r"(Максимум(?:\s|</?[^>]+>|[*_])*)([0-9]+(?:[.,][0-9]+)?)",
                    rf"\g<1>{formatted_max}",
                    val,
                    flags=re.IGNORECASE,
                )
            if state_id in self._MIN_AMOUNT_STATE_IDS and min_amount is not None:
                val = re.sub(
                    r"(Минимум(?:\s*:\s*|\s+))([0-9]+(?:[.,][0-9]+)?)",
                    rf"\g<1>{formatted_min}",
                    val,
                    flags=re.IGNORECASE,
                )
                val = re.sub(
                    r"(от\s+)([0-9]+(?:[.,][0-9]+)?)(\s+[A-Z]{2,5})",
                    rf"\g<1>{formatted_min}\g<3>",
                    val,
                    flags=re.IGNORECASE,
                )
            themed[key] = val
        return themed

    def _format_dynamic_limit(self, amount: float, *, coin: str) -> str:
        if (coin or "").upper() == "USDT":
            return f"{amount:.2f}"
        return f"{amount:.8f}"

    def _extract_coin_from_state_text(self, state_text_upper: str) -> str:
        if any(k in state_text_upper for k in ("USDT", "TETHER", "TRC20", "BSC20", "BEP20")):
            return "USDT"
        if "BTC" in state_text_upper or "BITCOIN" in state_text_upper:
            return "BTC"
        if "LTC" in state_text_upper or "LITECOIN" in state_text_upper:
            return "LTC"
        if "ETH" in state_text_upper or "ETHEREUM" in state_text_upper:
            return "ETH"
        if "XMR" in state_text_upper or "MONERO" in state_text_upper:
            return "XMR"
        if "TRX" in state_text_upper or "TRON" in state_text_upper:
            return "TRX"
        if "TON" in state_text_upper:
            return "TON"
        if "RUB" in state_text_upper or "₽" in state_text_upper:
            return "RUB"
        return ""

    def _state_has_only_system_next(self, state_id: str) -> bool:
        action_map = self.catalog.transition_index.get(state_id) or {}
        return bool(action_map) and set(action_map.keys()) == {"<next-message>"}

    def _state_explicitly_requests_text_input(self, state_id: str) -> bool:
        state = self.catalog.states.get(state_id) or {}
        text_blob = "\n".join(
            [
                str(state.get("text") or ""),
                str(state.get("text_html") or ""),
                str(state.get("text_markdown") or ""),
            ]
        ).lower()
        # Fallback is intentionally narrow: only wallet/address collection states.
        # Amount states must continue to use existing validation/error routing.
        return any(hint in text_blob for hint in ("кошелек", "адрес", "wallet", "address"))

    def _coin_media_relpath(self, *, coin: str, role: str) -> str | None:
        return f"media/coin_{coin.lower()}_{role}.jpg"

    async def _get_live_rates_rub(self) -> dict[str, float]:
        rates = await self.app_context.rates.get_rates()
        return {
            symbol: float(rates.get(symbol.lower(), 0.0))
            for symbol in FLOW_CATALOG_RE_MAP.keys()
        }

    def _effective_requisites_for_state(self, session: UserSession | None, state_id: str) -> str:
        settings = self.app_context.settings
        if settings.requisites_mode == "single":
            return settings.requisites_value

        if session and session.selected_payment_method:
            _, value = settings.method_requisites(session.selected_payment_method)
            if value.strip():
                return value

        state = self.catalog.states.get(state_id) or {}
        text_blob = "\n".join(
            [
                str(state.get("text") or ""),
                str(state.get("text_html") or ""),
                str(state.get("text_markdown") or ""),
            ]
        ).lower()
        matches: list[str] = []
        for method in settings.payment_methods():
            if method.lower() in text_blob:
                matches.append(method)
        if len(matches) == 1:
            _, value = settings.method_requisites(matches[0])
            if value.strip():
                return value

        return settings.requisites_value

    def _effective_bank_for_session(self, session: UserSession | None, state_id: str) -> str:
        settings = self.app_context.settings
        if settings.requisites_mode == "single":
            return settings.requisites_bank

        if session and session.selected_payment_method:
            bank, _ = settings.method_requisites(session.selected_payment_method)
            if bank.strip():
                return bank

        state = self.catalog.states.get(state_id) or {}
        text_blob = "\n".join(
            [
                str(state.get("text") or ""),
                str(state.get("text_html") or ""),
                str(state.get("text_markdown") or ""),
            ]
        ).lower()
        for method in settings.payment_methods():
            if method.lower() in text_blob:
                bank, _ = settings.method_requisites(method)
                if bank.strip():
                    return bank

        return settings.requisites_bank

    def _match_payment_method(self, action_text: str) -> str:
        action = (action_text or "").strip().lower()
        if not action:
            return ""
        for method in self.app_context.settings.payment_methods():
            if method.lower() == action:
                return method
        return ""

    def _default_payment_method(self) -> str:
        methods = self.app_context.settings.payment_methods()
        if methods:
            return methods[0]
        return "Перевод на карту"


def outgoing_text_from_state(catalog: FlowCatalog, state_id: str) -> str:
    state = catalog.states[state_id]
    return str(state.get("text") or "")


def _build_env_links(env: dict[str, Any], default_operator_url: str) -> dict[str, str]:
    links = dict(DEFAULT_LINKS)
    if default_operator_url.strip():
        links["operator"] = default_operator_url.strip()

    for link_key in DEFAULT_LINKS:
        env_key = f"{link_key.upper()}_LINK"
        value = str(env.get(env_key) or "").strip()
        if value:
            links[link_key] = value
    return links


async def amain() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    env_path = project_dir / ".env"
    load_dotenv(env_path, override=True)

    bot_token = (os.getenv("BOT_TOKEN") or "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is empty")

    catalog = FlowCatalog.from_directory(
        raw_dir=project_dir / "data" / "raw",
        media_dir=project_dir / "data" / "media",
    )

    env = dotenv_values(env_path)
    admin_ids = parse_admin_ids(str(env.get("ADMIN_IDS") or ""))
    default_commission = parse_non_negative_amount(str(env.get("DEFAULT_COMMISSION_PERCENT") or ""))
    if default_commission is None or default_commission < 0 or default_commission > 50:
        default_commission = 2.5

    settings_store = SettingsStore(
        path=project_dir / "data" / "admin" / "settings.json",
        default_commission=default_commission,
        env_links=_build_env_links(env, catalog.default_operator_url),
    )
    users_store = UsersStore(project_dir / "data" / "admin" / "users.json")
    orders_store = OrdersStore(project_dir / "data" / "admin" / "orders.json")
    sessions_store = SessionsStore(project_dir / "data" / "admin" / "sessions.json")
    media_store = MediaStore(project_dir / "data" / "admin" / "media_cache.json")
    
    async with httpx.AsyncClient() as http_client:
        rate_service = RateService(http_client=http_client, ttl_seconds=45)

        app_context = AppContext(
            settings=settings_store,
            users=users_store,
            orders=orders_store,
            sessions=sessions_store,
            media=media_store,
            rates=rate_service,
            http_client=http_client,
            admin_ids=admin_ids,
            env_path=env_path,
        )

        runtime = FlowRuntime(
            project_dir=project_dir,
            catalog=catalog,
            app_context=app_context,
        )
        await runtime.run_loops()

        dp = Dispatcher(storage=MemoryStorage())
        
        # Admin router must be included first or the catch-all @dp.message() will intercept its commands
        dp.include_router(build_admin_router(app_context))

        main_router = Router(name="main")

        @main_router.message(CommandStart())
        async def _start(message: Message) -> None:
            await runtime.start(message)

        @main_router.callback_query(F.data.startswith("a:"))
        async def _callback(cb: CallbackQuery) -> None:
            await runtime.on_callback(cb)

        @main_router.message()
        async def _message(message: Message) -> None:
            if (message.text or "").strip() == "/start":
                await runtime.start(message)
                return
            await runtime.on_message(message)

        dp.include_router(main_router)

        bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        
        logger.info("Bot is starting...")
        try:
            await dp.start_polling(bot)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            pass
        finally:
            logger.info("Shutting down...")
            await runtime.stop()
            await bot.session.close()
            logger.info("Bye!")


def run() -> None:
    asyncio.run(amain())
