from datetime import datetime, timezone

def agora_utc() -> datetime:
    return datetime.now(tz=timezone.utc)

def normalizar_dt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt