"""
Gumroad webhook handler.

Receives sale notifications and upgrades users to Pro.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/gumroad")
async def gumroad_webhook(request: Request):
    """
    Handle Gumroad sale webhook.

    Gumroad sends form-encoded or JSON body with:
      - product_id, product_permalink, email, sale_id, etc.

    Verification: check product_id matches our configured product.
    """
    try:
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to parse webhook body")
        return JSONResponse(status_code=400, content={"error": "Invalid body"})

    # Verify product
    product_id = str(body.get("product_id", ""))
    if config.GUMROAD_PRODUCT_ID and product_id != config.GUMROAD_PRODUCT_ID:
        logger.warning("Webhook product_id mismatch: %s", product_id)
        return JSONResponse(status_code=403, content={"error": "Product mismatch"})

    email = body.get("email", "").strip().lower()
    sale_id = body.get("sale_id", "")

    if not email:
        return JSONResponse(status_code=400, content={"error": "Missing email"})

    logger.info("Gumroad sale: email=%s sale_id=%s", email, sale_id)

    # Try to find existing user and upgrade
    from models import get_profile_by_email, upgrade_to_pro, create_pending_entitlement, log_usage

    profile = get_profile_by_email(email)
    if profile:
        if profile.get("tier") != "pro":
            upgrade_to_pro(profile["id"])
            log_usage(user_id=profile.get("auth_user_id"), sale_id=sale_id)
            logger.info("Upgraded %s to pro", email)
            return {"status": "upgraded", "email": email}
        return {"status": "already_pro", "email": email}

    # No account yet — store pending entitlement
    create_pending_entitlement(email)
    logger.info("Stored pending entitlement for %s", email)
    return {"status": "pending", "email": email}
