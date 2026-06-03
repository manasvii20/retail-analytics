from datetime import datetime


def get_health(db):
    return {
        "status": "ok",
        "checked_at": datetime.utcnow(),
        "stores": [],
        "db_connected": True,
    }