"""
Scatter-Gather: notifica todos os grupos registrados via HTTP POST
para /rides/incoming em paralelo, com timeout por parceiro.

Falhas individuais são isoladas — um parceiro offline não
interrompe a notificação dos demais.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.core.http_client import http_client
from app.core.settings import get_settings
from app.models.group import Group

logger = logging.getLogger(__name__)


@dataclass
class NotificationResult:
    """Resultado da tentativa de notificação de um parceiro."""
    group_id: str
    success: bool
    status_code: Optional[int] = None
    error: Optional[str] = None
    response_time_ms: Optional[float] = None


async def _notify_one(
    group: Group,
    payload: dict,
    timeout: float,
) -> NotificationResult:
    """Envia POST /rides/incoming para um único grupo."""
    import time

    url = f"{group.service_url.rstrip('/')}/rides/incoming"
    start = time.monotonic()

    try:
        response = await http_client.post(url, json=payload, timeout=timeout)
        elapsed = (time.monotonic() - start) * 1000
        response.raise_for_status()

        logger.info(
            "Grupo notificado | group=%s status=%d tempo=%.0fms",
            group.group_id, response.status_code, elapsed,
        )
        return NotificationResult(
            group_id=group.group_id,
            success=True,
            status_code=response.status_code,
            response_time_ms=elapsed,
        )

    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        logger.warning(
            "Falha ao notificar grupo | group=%s url=%s erro=%s tempo=%.0fms",
            group.group_id, url, exc, elapsed,
        )
        return NotificationResult(
            group_id=group.group_id,
            success=False,
            error=str(exc),
            response_time_ms=elapsed,
        )


async def scatter_gather_notify(
    groups: list[Group],
    ride_uuid: str,
    origin: dict,
    destination: dict,
    origin_service_id: str,
    passenger_id: str,
    logical_timestamp: int,
    auction_deadline: datetime,
) -> list[NotificationResult]:
    """
    Dispara POST /rides/incoming para todos os grupos em paralelo.

    Retorna a lista de resultados individuais — útil para logging
    e para registrar response_time_ms nas propostas.
    """
    import asyncio

    settings = get_settings()

    if not groups:
        logger.info("Nenhum grupo registrado para notificar | ride=%s", ride_uuid)
        return []

    payload = {
        "rideUuid": ride_uuid,
        "origin": origin,
        "destination": destination,
        "originServiceId": origin_service_id,
        "passengerId": passenger_id,
        "logicalTimestamp": logical_timestamp,
        "auctionDeadline": auction_deadline.isoformat(),
    }

    tasks = [
        _notify_one(group, payload, settings.partner_request_timeout_seconds)
        for group in groups
    ]

    results: list[NotificationResult] = await asyncio.gather(
        *tasks, return_exceptions=False
        # return_exceptions=False é seguro pois _notify_one nunca lança —
        # captura internamente e retorna NotificationResult(success=False)
    )

    total = len(results)
    ok = sum(1 for r in results if r.success)
    logger.info(
        "Scatter-gather concluído | ride=%s notificados=%d/%d",
        ride_uuid, ok, total,
    )
    return results