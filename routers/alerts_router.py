# =============================================================================
# routers/alerts_router.py — Alert Rules & Notification Endpoints
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import logging

from sqlalchemy.orm import Session
from database import get_db
from models import AlertRule, AlertHistory, AlertConditionType, AlertChannel, Notification, User
from auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])


# =============================================================================
# SCHEMAS
# =============================================================================

class AlertRuleCreate(BaseModel):
    label:            str
    account_id:       Optional[str] = None   # None = all accounts
    condition_type:   str
    threshold_value:  Optional[float] = None
    threshold_unit:   Optional[str] = "dollar"  # "dollar" or "pct"
    channels:         List[str] = ["in_app"]
    cooldown_min:     int = 15
    quiet_hours_override: bool = False


class AlertRuleUpdate(BaseModel):
    label:           Optional[str] = None
    threshold_value: Optional[float] = None
    threshold_unit:  Optional[str] = None
    channels:        Optional[List[str]] = None
    cooldown_min:    Optional[int] = None
    is_active:       Optional[bool] = None
    quiet_hours_override: Optional[bool] = None


# =============================================================================
# ALERT RULES CRUD
# =============================================================================

@router.post("/rules", status_code=201, summary="Create Alert Rule")
async def create_rule(
    req: AlertRuleCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        condition = AlertConditionType(req.condition_type)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown condition type: {req.condition_type}")

    channels = []
    for ch in req.channels:
        try:
            channels.append(AlertChannel(ch))
        except ValueError:
            pass

    rule = AlertRule(
        user_id=current_user.id,
        account_id=req.account_id,
        label=req.label,
        condition_type=condition,
        threshold_value=req.threshold_value,
        threshold_unit=req.threshold_unit,
        channels=channels,
        cooldown_min=req.cooldown_min,
        quiet_hours_override=req.quiet_hours_override,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    return _format_rule(rule)


@router.get("/rules", summary="List Alert Rules")
async def list_rules(
    account_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(AlertRule).filter(AlertRule.user_id == current_user.id)
    if account_id:
        q = q.filter(AlertRule.account_id == account_id)
    rules = q.order_by(AlertRule.created_at.desc()).all()
    return [_format_rule(r) for r in rules]


@router.patch("/rules/{rule_id}", summary="Update Alert Rule")
async def update_rule(
    rule_id: str,
    req: AlertRuleUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.query(AlertRule).filter(
        AlertRule.id == rule_id,
        AlertRule.user_id == current_user.id,
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if req.label is not None:            rule.label = req.label
    if req.threshold_value is not None:  rule.threshold_value = req.threshold_value
    if req.threshold_unit is not None:   rule.threshold_unit = req.threshold_unit
    if req.cooldown_min is not None:     rule.cooldown_min = req.cooldown_min
    if req.is_active is not None:        rule.is_active = req.is_active
    if req.quiet_hours_override is not None: rule.quiet_hours_override = req.quiet_hours_override
    if req.channels is not None:
        rule.channels = [AlertChannel(c) for c in req.channels if c in [e.value for e in AlertChannel]]

    db.commit()
    return _format_rule(rule)


@router.delete("/rules/{rule_id}", summary="Delete Alert Rule")
async def delete_rule(
    rule_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.query(AlertRule).filter(
        AlertRule.id == rule_id, AlertRule.user_id == current_user.id
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"message": "Rule deleted"}


@router.get("/rules/{rule_id}/history", summary="Alert Trigger History")
async def rule_history(
    rule_id: str,
    limit: int = Query(50, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.query(AlertRule).filter(
        AlertRule.id == rule_id, AlertRule.user_id == current_user.id
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    history = db.query(AlertHistory).filter(
        AlertHistory.rule_id == rule_id
    ).order_by(AlertHistory.triggered_at.desc()).limit(limit).all()

    return [
        {
            "id":            h.id,
            "triggered_at":  h.triggered_at.isoformat(),
            "trigger_value": h.trigger_value,
            "message":       h.message,
            "channel":       h.channel.value,
            "status":        h.status.value,
            "error_message": h.error_message,
        }
        for h in history
    ]


# =============================================================================
# NOTIFICATIONS (In-App)
# =============================================================================

@router.get("/notifications", summary="In-App Notifications")
async def get_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        q = q.filter(Notification.is_read == False)
    notifications = q.order_by(Notification.created_at.desc()).limit(limit).all()
    unread_count = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
    ).count()

    return {
        "unread_count": unread_count,
        "notifications": [
            {
                "id":         n.id,
                "title":      n.title,
                "message":    n.message,
                "category":   n.category,
                "is_read":    n.is_read,
                "link":       n.link,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ],
    }


@router.post("/notifications/read-all", summary="Mark All Notifications Read")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()
    return {"message": "All notifications marked as read"}


@router.patch("/notifications/{notif_id}/read", summary="Mark Notification Read")
async def mark_read(
    notif_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.user_id == current_user.id,
    ).first()
    if notif:
        notif.is_read = True
        db.commit()
    return {"message": "Marked as read"}


@router.get("/conditions", summary="List Available Alert Condition Types")
async def list_conditions():
    """Return all supported alert condition types with descriptions."""
    return [
        {"value": c.value, "label": c.value.replace("_", " ").title()}
        for c in AlertConditionType
    ]


@router.get("/channels", summary="List Available Alert Channels")
async def list_channels():
    return [{"value": c.value, "label": c.value.replace("_", " ").title()} for c in AlertChannel]


# =============================================================================
# HELPERS
# =============================================================================

def _format_rule(rule: AlertRule) -> dict:
    return {
        "id":             rule.id,
        "user_id":        rule.user_id,
        "account_id":     rule.account_id,
        "label":          rule.label,
        "condition_type": rule.condition_type.value,
        "threshold_value": rule.threshold_value,
        "threshold_unit": rule.threshold_unit,
        "channels":       [c.value if hasattr(c, 'value') else c for c in (rule.channels or [])],
        "is_active":      rule.is_active,
        "cooldown_min":   rule.cooldown_min,
        "last_triggered": rule.last_triggered.isoformat() if rule.last_triggered else None,
        "trigger_count":  rule.trigger_count,
        "quiet_hours_override": rule.quiet_hours_override,
        "created_at":     rule.created_at.isoformat() if rule.created_at else None,
    }
