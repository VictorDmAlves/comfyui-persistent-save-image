from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable


class GalleryStore:
    """Small, thread-safe JSON store containing only ComfyUI image references."""

    _VERSION = 1

    def __init__(self, root: str | os.PathLike[str], max_images: int = 10) -> None:
        self.root = Path(root)
        self.max_images = max(1, int(max_images))
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalise_gallery_id(gallery_id: Any) -> str:
        value = str(gallery_id or "").strip()
        if not value:
            raise ValueError("gallery_id is empty")
        if len(value) > 512:
            raise ValueError("gallery_id is too long")
        return value

    @classmethod
    def _filename_for_id(cls, gallery_id: str) -> str:
        digest = hashlib.sha256(gallery_id.encode("utf-8")).hexdigest()
        return f"{digest}.json"

    def _path(self, gallery_id: str) -> Path:
        clean_id = self._normalise_gallery_id(gallery_id)
        return self.root / self._filename_for_id(clean_id)

    @staticmethod
    def _normalise_image(item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None

        filename = item.get("filename")
        if not isinstance(filename, str) or not filename:
            return None

        subfolder = item.get("subfolder", "")
        image_type = item.get("type", "output")
        if not isinstance(subfolder, str):
            subfolder = str(subfolder or "")
        if not isinstance(image_type, str):
            image_type = str(image_type or "output")

        return {
            "filename": filename,
            "subfolder": subfolder,
            "type": image_type,
        }

    @classmethod
    def _deduplicate(cls, images: Iterable[Any]) -> list[dict[str, str]]:
        # Retain chronological order while making the newest duplicate win.
        normalised: list[dict[str, str]] = []
        newest_index: dict[tuple[str, str, str], int] = {}

        for raw in images:
            item = cls._normalise_image(raw)
            if item is None:
                continue
            key = (item["type"], item["subfolder"], item["filename"])
            if key in newest_index:
                normalised[newest_index[key]] = None  # type: ignore[assignment]
            newest_index[key] = len(normalised)
            normalised.append(item)

        return [item for item in normalised if item is not None]

    def _read_unlocked(self, gallery_id: str) -> list[dict[str, str]]:
        path = self._path(gallery_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # A corrupt manifest should never prevent image generation.
            return []

        if not isinstance(payload, dict):
            return []
        images = self._deduplicate(payload.get("images", []))
        return images[-self.max_images :]

    def read(self, gallery_id: str) -> list[dict[str, str]]:
        with self._lock:
            return self._read_unlocked(gallery_id)

    def append(
        self, gallery_id: str, new_images: Iterable[Any]
    ) -> list[dict[str, str]]:
        clean_id = self._normalise_gallery_id(gallery_id)
        with self._lock:
            previous = self._read_unlocked(clean_id)
            merged = self._deduplicate([*previous, *list(new_images)])
            retained = merged[-self.max_images :]
            self._write_unlocked(clean_id, retained)
            return retained

    def reset(self, gallery_id: str) -> None:
        path = self._path(gallery_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _write_unlocked(
        self, gallery_id: str, images: list[dict[str, str]]
    ) -> None:
        path = self._path(gallery_id)
        payload = {
            "version": self._VERSION,
            "gallery_id": gallery_id,
            "updated_at": time.time(),
            "images": images,
        }

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-", suffix=".tmp", dir=str(self.root)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
