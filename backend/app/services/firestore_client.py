"""
Firestore client — uses firebase-admin with Application Default Credentials
or a service account JSON specified by GOOGLE_APPLICATION_CREDENTIALS.
"""
import os
import logging
from typing import Optional
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from app.models.match import MatchAnalytics, MatchStatus

logger = logging.getLogger(__name__)

# Initialise once
if not firebase_admin._apps:
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path and os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
    else:
        cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {
        "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    })

_db = firestore.client()


def update_match_status(
    match_id: str,
    status: MatchStatus,
    progress: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Update match processing status in Firestore."""
    ref = _db.collection("matches").document(match_id)
    update_data: dict = {
        "status": status.value,
        "updatedAt": SERVER_TIMESTAMP,
    }
    if progress is not None:
        update_data["processingProgress"] = progress
    if error_message:
        update_data["errorMessage"] = error_message

    ref.update(update_data)
    logger.info("Match status updated", extra={"matchId": match_id, "status": status.value})


def write_match_analytics(match_id: str, analytics: MatchAnalytics) -> None:
    """Write completed analytics to Firestore atomically."""
    ref = _db.collection("matches").document(match_id)
    stats_dict = analytics.model_dump()
    ref.update({
        "status": MatchStatus.COMPLETED.value,
        "stats": stats_dict,
        "processingProgress": 100,
        "updatedAt": SERVER_TIMESTAMP,
    })
    logger.info("Analytics written to Firestore", extra={"matchId": match_id})


def append_audit_log(match_id: str, user_id: str, event_type: str, metadata: dict = {}) -> None:
    """Append an immutable audit log entry."""
    _db.collection("audit").add({
        "type": event_type,
        "matchId": match_id,
        "userId": user_id,
        "metadata": metadata,
        "timestamp": SERVER_TIMESTAMP,
    })
