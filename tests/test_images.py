from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from app import images


class _FakeImage:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _FakeImageModule:
    @staticmethod
    def open(path: Path) -> _FakeImage:
        return _FakeImage()


class _FakeTesseractNotFoundError(Exception):
    pass


class _FakePytesseract:
    @staticmethod
    def image_to_string(image: object, lang: str) -> str:
        raise _FakeTesseractNotFoundError("missing")


class _FakeWrappedMissingBinaryPytesseract:
    @staticmethod
    def image_to_string(image: object, lang: str) -> str:
        try:
            raise FileNotFoundError(2, "No such file or directory", "tesseract")
        except FileNotFoundError as exc:
            raise RuntimeError("ocr wrapper failed") from exc


def test_extract_ocr_text_handles_missing_tesseract_once(monkeypatch, caplog) -> None:
    monkeypatch.setattr(images, "Image", _FakeImageModule)
    monkeypatch.setattr(images, "pytesseract", _FakePytesseract)
    monkeypatch.setattr(images, "TesseractNotFoundError", _FakeTesseractNotFoundError)
    monkeypatch.setattr(images, "_ocr_binary_missing_logged", False)

    with caplog.at_level(logging.WARNING):
        assert images.extract_ocr_text(Path("fake.png")) is None
        assert images.extract_ocr_text(Path("fake.png")) is None

    warnings = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert warnings.count("OCR disabled: tesseract binary not found in PATH") == 1


def test_extract_ocr_text_handles_wrapped_missing_binary_error(monkeypatch, caplog) -> None:
    monkeypatch.setattr(images, "Image", _FakeImageModule)
    monkeypatch.setattr(images, "pytesseract", _FakeWrappedMissingBinaryPytesseract)
    monkeypatch.setattr(images, "TesseractNotFoundError", None)
    monkeypatch.setattr(images, "_ocr_binary_missing_logged", False)

    with caplog.at_level(logging.WARNING):
        assert images.extract_ocr_text(Path("fake.png")) is None

    warnings = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert "OCR disabled: tesseract binary not found in PATH" in warnings


def test_get_visual_file_info_uses_video_thumbnail() -> None:
    message = SimpleNamespace(
        photo=None,
        animation=None,
        video=SimpleNamespace(
            thumbnail=SimpleNamespace(file_id="thumb_1", file_size=12345),
        ),
        document=None,
    )
    assert images.get_visual_file_info(message) == ("thumb_1", 12345)
