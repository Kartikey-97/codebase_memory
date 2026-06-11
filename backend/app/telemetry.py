import logging
from datetime import datetime, UTC
from typing import Any
from bson import ObjectId

from app.config import get_settings
from app.db.mcp_mongo import create_mcp_client

import re

logger = logging.getLogger(__name__)

def _scrub_pii(text: str) -> str:
    """Scrub sensitive information before storage."""
    # JWTs
    text = re.sub(r'ey[a-zA-Z0-9_-]+\.ey[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', '[JWT MASKED]', text)
    # Emails
    text = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[EMAIL MASKED]', text)
    # AWS Keys
    text = re.sub(r'(AKIA|ASIA)[A-Z0-9]{16}', '[AWS KEY MASKED]', text)
    # GitHub Tokens
    text = re.sub(r'ghp_[A-Za-z0-9]{36}', '[GITHUB TOKEN MASKED]', text)
    # Bearer Tokens
    text = re.sub(r'Bearer\s+[A-Za-z0-9\-\._~+]+', 'Bearer [TOKEN MASKED]', text, flags=re.IGNORECASE)
    # Generic Secrets
    text = re.sub(r'(?i)(password|secret|api[_-]?key)["\s:=]+[\'"]?([^\'"\s]+)[\'"]?', r'\1: [SECRET MASKED]', text)
    return text

async def record_telemetry(payload: dict[str, Any]) -> None:
    """Asynchronously record telemetry without blocking the user response."""
    try:
        settings = get_settings()
        mcp_client = create_mcp_client()
        
        # Scrub long queries
        if "query" in payload and "text_scrubbed" in payload["query"]:
            text = payload["query"]["text_scrubbed"]
            text = _scrub_pii(text)
            if len(text) > 250:
                payload["query"]["text_scrubbed"] = text[:250] + "... [TRUNCATED]"
            else:
                payload["query"]["text_scrubbed"] = text
                
        payload["_id"] = str(ObjectId())
        payload["timestamp"] = datetime.now(UTC).isoformat()
        
        await mcp_client.insert_many(
            database=settings.mongodb_db_name,
            collection="query_telemetry",
            documents=[payload]
        )
    except Exception as e:
        # Fail silently for telemetry to avoid breaking user workflows
        logger.error(f"Failed to record telemetry: {e}")
