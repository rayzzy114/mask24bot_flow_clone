from __future__ import annotations

import logging
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router

logger = logging.getLogger(__name__)

# Configure basic logging if not already done
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
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
from .storage import OrdersStore, SettingsStore, UsersStore
from .tokens import TokenRegistry
from .utils import (
    is_valid_crypto_address,
    parse_admin_ids,
    parse_non_negative_amount,
    safe_username,
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
                self.sessions[int(uid_str)] = UserSession.from_dict(data)
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
                self.app_context.sessions.update_session(uid, session.to_dict())
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
                if not isinstance(row, list): continue
                for btn in row:
                    if not isinstance(btn, dict): continue
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
                if not text: continue
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

        token = str(cb.data or "")
        action_text = self.tokens.get_action(token)
        if not action_text:
            await cb.answer()
            return

        callback_message = cb.message if isinstance(cb.message, Message) else None

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
        if action_text in ("🔙", "🔙 Назад", "Назад", "Back"):
            prev_state = self._resolve_back_state(session, action_text)
            if prev_state and callback_message is not None:
                await self._send_state_by_id(callback_message, prev_state, session=session)
                await cb.answer()
                return

        selected_method = self._match_payment_method(action_text)
        if selected_method:
            session.selected_payment_method = selected_method

        if action_text == "✅ Я оплатил" and callback_message is not None:
            session.awaiting_payment_proof = True
            session.payment_context = self._state_text(session.state_id)
            await callback_message.answer(PAYMENT_PROOF_PROMPT)
            await cb.answer()
            return

        next_state = self.catalog.resolve_action(session.state_id, action_text)
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
            if msg.photo:
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
        if text in ("🔙", "🔙 Назад", "Назад", "Back", "❌ Отмена", "Отмена", "Cancel"):
            prev_state = self._resolve_back_state(session, text)
            if prev_state:
                await self._send_state_by_id(msg, prev_state, session=session)
                return

        selected_method = self._match_payment_method(text)
        if selected_method:
            session.selected_payment_method = selected_method

        if text == "✅ Я оплатил":
            session.awaiting_payment_proof = True
            session.payment_context = self._state_text(session.state_id)
            await msg.answer(PAYMENT_PROOF_PROMPT)
            return

        # 3. Resolve Transition
        next_state = self.catalog.resolve_action(session.state_id, text)
        
        # If no explicit action, but state accepts input, we validate and use <manual-input>/<input>
        if next_state is None and self.catalog.state_accepts_input(session.state_id):
            # Input validation (only if we have text)
            if text and not self._validate_input(session.state_id, text):
                error_state = self._find_error_state(session.state_id, text)
                if error_state:
                    await self._send_state_by_id(msg, error_state, session=session)
                    return
                # Если специфичный error_state не найден, даем общий ответ
                await msg.answer("⚠️ Введенные данные некорректны. Пожалуйста, проверьте формат и попробуйте снова.")
                return

            # If it's a photo, we also allow transition if text is empty
            if text or msg.photo:
                if msg.photo and not session.awaiting_payment_proof:
                    # Forward non-payment photo to admins as 'general input'
                    await self._forward_general_photo(msg, session)
                
                next_state = self.catalog.resolve_action(session.state_id, text, is_text_input=True)

        if not next_state:
            # If no transition found, check if this text might be a known button in start state 
            # (sometimes users type buttons that are not in current state but are 'global' in their mind)
            return

        session.push_state(next_state)
        session.awaiting_payment_proof = False
        await self._send_state_by_id(msg, next_state, session=session)
        await self._send_system_chain(msg, session)

    def _resolve_back_state(self, session: UserSession, action_text: str) -> str | None:
        """Try to find an explicit 'Back' edge, otherwise pop history."""
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

    def _validate_input(self, state_id: str, text: str) -> bool:
        """Validate input based on state text hints and checksums."""
        state_text = self._state_text(state_id).upper()
        
        # Check for address patterns
        for coin, regex in FLOW_CATALOG_RE_MAP.items():
            if coin in state_text and ("АДРЕС" in state_text or "WALLET" in state_text or "КОШЕЛЕК" in state_text or "ПРИСЛАТЬ" in state_text):
                # We found a coin hint, now use strict checksum validation
                return is_valid_crypto_address(text, coin)
        
        # If it looks like an address input but coin not found in text, try all known crypto
        if ("АДРЕС" in state_text or "WALLET" in state_text or "КОШЕЛЕК" in state_text) and len(text) > 20 and " " not in text:
            # Try BTC and TRX/USDT as most common
            if is_valid_crypto_address(text, "BTC"): return True
            if is_valid_crypto_address(text, "TRX"): return True
            if is_valid_crypto_address(text, "ETH"): return True
            return False

        # Check for amount patterns
        if "СУММ" in state_text or "ВВЕДИТЕ" in state_text or "AMOUNT" in state_text:
            try:
                val_str = text.replace(",", ".").replace(" ", "")
                val = float(val_str)
                # Ensure it's not a tiny/dust amount for BTC
                if "BTC" in state_text and val < 0.00001: return False
                return val > 0
            except ValueError:
                return False
                
        return True

    def _find_error_state(self, state_id: str, text: str) -> str | None:
        """Try to find a state in edges that looks like an error state for this input."""
        action_map = self.transition_index.get(state_id) or {}
        
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
        if not photos or msg.bot is None: return
        user = msg.from_user
        if not user: return
        
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

        await send_state(
            msg,
            state,
            media_dir=self.media_dir,
            media_store=self.app_context.media,
            token_by_action=self.tokens.get_token,
        )

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
