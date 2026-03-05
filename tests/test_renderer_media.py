from __future__ import annotations

import asyncio
from pathlib import Path

from app.renderer import send_state


class DummyMessage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.calls.append(("answer", str(text)))

    async def answer_photo(self, photo, caption=None, reply_markup=None, parse_mode=None):
        self.calls.append(("answer_photo", str(caption) if caption is not None else None))

    async def answer_video(self, video, caption=None, reply_markup=None, parse_mode=None):
        self.calls.append(("answer_video", str(caption) if caption is not None else None))

    async def answer_document(self, document, caption=None, reply_markup=None, parse_mode=None):
        self.calls.append(("answer_document", str(caption) if caption is not None else None))


class FlakyMediaMessage(DummyMessage):
    def __init__(self) -> None:
        super().__init__()
        self.photo_calls = 0

    async def answer_photo(self, photo, caption=None, reply_markup=None, parse_mode=None):
        self.photo_calls += 1
        if self.photo_calls == 1 and isinstance(photo, str):
            raise RuntimeError("bad cached file id")
        await super().answer_photo(photo, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)


def test_send_state_uses_media_when_relpath_is_string(tmp_path: Path) -> None:
    (tmp_path / "photo_1.jpg").write_bytes(b"jpg")
    state = {
        "text": "caption",
        "text_html": "caption",
        "media": "media/photo_1.jpg",
    }
    msg = DummyMessage()
    from app.storage import MediaStore
    media_store = MediaStore(tmp_path / "media_cache.json")

    asyncio.run(send_state(msg, state, media_dir=tmp_path, media_store=media_store, token_by_action=lambda x: x))

    assert msg.calls
    assert msg.calls[0][0] == "answer_photo"


def test_send_state_retries_cached_media_id_only_once(tmp_path: Path) -> None:
    (tmp_path / "photo_1.jpg").write_bytes(b"jpg")
    state = {
        "text": "caption",
        "text_html": "caption",
        "media": "media/photo_1.jpg",
    }
    msg = FlakyMediaMessage()
    from app.storage import MediaStore
    media_store = MediaStore(tmp_path / "media_cache.json")
    asyncio.run(media_store.set_file_id("photo_1.jpg", "stale_file_id"))

    asyncio.run(send_state(msg, state, media_dir=tmp_path, media_store=media_store, token_by_action=lambda x: x))

    assert msg.photo_calls == 2
    assert media_store.get_file_id("photo_1.jpg") == ""
