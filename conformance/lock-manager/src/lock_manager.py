"""
Lock Manager — árbitro de locks distribuídos do RideFleet Core.

Implementa aquisição e liberação de locks sobre IDs de corrida com TTL,
evitando aceitações duplicadas entre serviços concorrentes.

Esta implementação usa armazenamento in-memory com expiração por TTL.
Para produção, substituir pelo backend definido no ADR-002 (Redis/etcd/etc.).
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Lock:
    ride_id: str
    service_id: str
    acquired_at: float
    ttl_seconds: int
    expires_at: float = field(init=False)

    def __post_init__(self):
        self.expires_at = self.acquired_at + self.ttl_seconds

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class LockManager:
    """
    Árbitro de locks distribuídos com TTL.

    Thread-safe. O reaper thread limpa locks expirados periodicamente.
    """

    DEFAULT_TTL_SECONDS = 30
    REAPER_INTERVAL_SECONDS = 5

    def __init__(self):
        self._locks: dict[str, Lock] = {}
        self._mutex = threading.Lock()
        self._start_reaper()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(
        self,
        ride_id: str,
        service_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> bool:
        """
        Tenta adquirir o lock para ride_id em nome de service_id.

        Retorna True se o lock foi adquirido, False se já está detido
        por outro serviço e não expirou.

        Um serviço pode reacquirir seu próprio lock (renovação de TTL).
        """
        with self._mutex:
            existing = self._locks.get(ride_id)

            if existing is not None and not existing.is_expired():
                # Lock ativo detido por outro serviço
                if existing.service_id != service_id:
                    return False
                # Mesmo serviço: renova TTL
                existing.acquired_at = time.time()
                existing.expires_at = existing.acquired_at + ttl_seconds
                existing.ttl_seconds = ttl_seconds
                return True

            # Lock livre ou expirado: adquire
            self._locks[ride_id] = Lock(
                ride_id=ride_id,
                service_id=service_id,
                acquired_at=time.time(),
                ttl_seconds=ttl_seconds,
            )
            return True

    def release(self, ride_id: str, service_id: str) -> bool:
        """
        Libera o lock para ride_id.

        Retorna True se liberado com sucesso. Retorna False se o lock
        não existe, já expirou, ou o service_id não é o detentor.
        """
        with self._mutex:
            existing = self._locks.get(ride_id)

            if existing is None:
                return False

            if existing.is_expired():
                del self._locks[ride_id]
                return False

            if existing.service_id != service_id:
                return False

            del self._locks[ride_id]
            return True

    def get_lock(self, ride_id: str) -> Optional[Lock]:
        """Retorna o lock ativo para ride_id, ou None se livre/expirado."""
        with self._mutex:
            lock = self._locks.get(ride_id)
            if lock is None:
                return None
            if lock.is_expired():
                del self._locks[ride_id]
                return None
            return lock

    def is_held_by(self, ride_id: str, service_id: str) -> bool:
        """Verifica se ride_id está bloqueado pelo service_id especificado."""
        lock = self.get_lock(ride_id)
        return lock is not None and lock.service_id == service_id

    def stats(self) -> dict:
        """Retorna métricas básicas do lock manager (para Prometheus)."""
        with self._mutex:
            active = sum(1 for l in self._locks.values() if not l.is_expired())
            expired = len(self._locks) - active
            return {"active_locks": active, "expired_locks_pending_cleanup": expired}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reap_expired(self):
        """Remove locks expirados do armazenamento."""
        with self._mutex:
            expired_keys = [
                rid for rid, lock in self._locks.items() if lock.is_expired()
            ]
            for key in expired_keys:
                del self._locks[key]

    def _reaper_loop(self):
        while True:
            time.sleep(self.REAPER_INTERVAL_SECONDS)
            self._reap_expired()

    def _start_reaper(self):
        t = threading.Thread(target=self._reaper_loop, daemon=True)
        t.start()
