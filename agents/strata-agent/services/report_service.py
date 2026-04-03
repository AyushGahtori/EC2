"""Attachment processing for strata-agent upload/report actions."""

from __future__ import annotations

from typing import Any


def normalize_attachments(raw_attachments: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not raw_attachments:
        return [], [{"name": "unknown", "reason": "No attachments were provided."}]

    processed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    for item in raw_attachments:
        name = str(item.get("name") or "unnamed-file")
        extracted = item.get("extractedText")
        if isinstance(extracted, str) and extracted.strip():
            processed.append(
                {
                    "name": name,
                    "mimeType": item.get("mimeType"),
                    "size": item.get("size"),
                    "storagePath": item.get("storagePath"),
                    "excerpt": extracted.strip()[:12000],
                    "hasExtractedText": True,
                }
            )
            continue
        processed.append(
            {
                "name": name,
                "mimeType": item.get("mimeType"),
                "size": item.get("size"),
                "storagePath": item.get("storagePath"),
                "excerpt": (
                    f"Uploaded file '{name}' is available, but inline extracted text was not provided "
                    "to strata-agent in this request."
                ),
                "hasExtractedText": False,
            }
        )
        failed.append({"name": name, "reason": "Inline text extraction unavailable; summarized from metadata only."})

    return processed, failed
