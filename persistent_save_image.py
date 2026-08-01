from __future__ import annotations

import logging
import os
from typing import Any

from aiohttp import web

import folder_paths
import nodes
from server import PromptServer

from .config import GALLERY_STORAGE_DIR_NAME, MAX_GALLERY_IMAGES
from .gallery_store import GalleryStore

LOGGER = logging.getLogger(__name__)


def _get_store_directory() -> str:
    try:
        user_directory = folder_paths.get_user_directory()
    except (AttributeError, TypeError):
        user_directory = os.path.join(os.path.dirname(__file__), "data")
    return os.path.join(user_directory, GALLERY_STORAGE_DIR_NAME)


STORE = GalleryStore(_get_store_directory(), max_images=MAX_GALLERY_IMAGES)


class PersistentSaveImage(nodes.SaveImage):
    """Save Image compatible node whose UI output contains a persistent gallery."""

    @classmethod
    def INPUT_TYPES(cls):
        base = nodes.SaveImage.INPUT_TYPES()
        required = dict(base.get("required", {}))
        optional = dict(base.get("optional", {}))
        hidden = dict(base.get("hidden", {}))
        hidden["unique_id"] = "UNIQUE_ID"

        # This is a regular serialised widget, hidden by the accompanying JS.
        # Keeping it optional also lets API clients omit it safely.
        optional["gallery_id"] = (
            "STRING",
            {
                "default": "",
                "multiline": False,
                "tooltip": "Internal persistent gallery identifier.",
            },
        )
        return {"required": required, "optional": optional, "hidden": hidden}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "image"
    DESCRIPTION = (
        "Saves images like Save Image and displays the most recent images "
        "from this persistent gallery."
    )

    def save_images(
        self,
        images,
        filename_prefix: str = "ComfyUI",
        gallery_id: str = "",
        prompt: Any = None,
        extra_pnginfo: Any = None,
        unique_id: Any = None,
    ):
        if not str(gallery_id or "").strip():
            # The frontend normally assigns this before queueing. This fallback
            # keeps API workflows useful even when the JS extension is absent.
            gallery_id = f"legacy-node-{unique_id or 'default'}"

        result = super().save_images(
            images=images,
            filename_prefix=filename_prefix,
            prompt=prompt,
            extra_pnginfo=extra_pnginfo,
        )

        current_images = result.get("ui", {}).get("images", [])
        try:
            gallery = STORE.append(gallery_id, current_images)
        except Exception:
            LOGGER.exception("Could not update Persistent Save Image gallery")
            gallery = current_images[-max(1, int(MAX_GALLERY_IMAGES)) :]

        result.setdefault("ui", {})["images"] = gallery
        return result


@PromptServer.instance.routes.get("/persistent-save-image/gallery")
async def persistent_save_image_gallery(request: web.Request) -> web.Response:
    gallery_id = request.query.get("gallery_id", "")
    try:
        images = STORE.read(gallery_id)
    except ValueError as error:
        return web.json_response({"error": str(error), "images": []}, status=400)
    except Exception:
        LOGGER.exception("Could not read Persistent Save Image gallery")
        return web.json_response(
            {"error": "Could not read gallery", "images": []}, status=500
        )

    return web.json_response(
        {
            "gallery_id": gallery_id,
            "max_images": STORE.max_images,
            "images": images,
        }
    )


@PromptServer.instance.routes.post("/persistent-save-image/reset")
async def persistent_save_image_reset(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    gallery_id = payload.get("gallery_id", "") if isinstance(payload, dict) else ""
    try:
        STORE.reset(gallery_id)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    except Exception:
        LOGGER.exception("Could not reset Persistent Save Image gallery")
        return web.json_response({"error": "Could not reset gallery"}, status=500)

    return web.json_response({"ok": True, "gallery_id": gallery_id, "images": []})


NODE_CLASS_MAPPINGS = {"PersistentSaveImage": PersistentSaveImage}
NODE_DISPLAY_NAME_MAPPINGS = {"PersistentSaveImage": "Persistent Save Image"}
