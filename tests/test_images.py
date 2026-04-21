from __future__ import annotations

import logging
from pathlib import Path

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
