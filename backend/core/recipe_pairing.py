from __future__ import annotations

from typing import Final


COMPOSITION_TYPES: Final = ("荤", "素")
SERVING_TEMPERATURES: Final = ("热", "冷")
PRIMARY_COOKING_METHODS: Final = (
    "蒸",
    "煮",
    "炒",
    "炖",
    "煎",
    "炸",
    "烤",
    "拌",
    "烧焖",
    "冷制",
    "其他",
)
PAIRING_ATTRIBUTE_FIELDS: Final = (
    "composition_type",
    "serving_temperature",
    "primary_cooking_method",
)


__all__ = [
    "COMPOSITION_TYPES",
    "PAIRING_ATTRIBUTE_FIELDS",
    "PRIMARY_COOKING_METHODS",
    "SERVING_TEMPERATURES",
]
