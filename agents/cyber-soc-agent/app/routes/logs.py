"""Windows Event Log routes — new feature."""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.windows_logs import MAX_LOG_LIMIT, fetch_event_logs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/logs", tags=["Windows Logs"])

VALID_CHANNELS = {"Security", "System", "Application"}


def _parse_channels(channels_str: Optional[str]) -> List[str]:
    if not channels_str:
        return ["Security", "System", "Application"]
    parts = [c.strip() for c in channels_str.split(",") if c.strip()]
    valid = [c for c in parts if c in VALID_CHANNELS]
    if not valid:
        return ["Security", "System", "Application"]
    return valid


@router.get("/realtime")
async def realtime_logs(
    limit: int = Query(default=10, ge=1, le=MAX_LOG_LIMIT, description=f"Number of logs (1–{MAX_LOG_LIMIT})"),
    channels: Optional[str] = Query(
        default=None,
        description="Comma-separated channel names: Security,System,Application",
    ),
):
    """
    Fetch Windows Event Logs in real-time.

    - **limit**: how many entries to return (1–200, default 10)
    - **channels**: comma-separated list of channels (Security, System, Application)

    On Windows: uses win32evtlog for live data.
    On other platforms: returns simulated data for development/testing.
    """
    try:
        channel_list = _parse_channels(channels)
        result = fetch_event_logs(channels=channel_list, limit=limit)
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("Realtime log fetch error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch event logs")


@router.get("/channels")
async def list_channels():
    """List available Windows Event Log channels."""
    return {
        "channels": sorted(VALID_CHANNELS),
        "description": {
            "Security": "Authentication, authorization, audit events",
            "System": "OS-level events, service failures, hardware issues",
            "Application": "Application-level events and errors",
        },
    }
