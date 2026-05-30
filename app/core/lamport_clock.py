"""
Relógio lógico de Lamport — singleton global do core.

O clock é incrementado a cada evento local e atualizado quando o core
recebe um timestamp de um serviço externo (max + 1), garantindo a relação
happened-before entre eventos de diferentes processos.
"""

import asyncio
from app.core.metrics import lamport_clock_metric

class LamportClock:
    """Implementação thread-safe do relógio lógico de Lamport para uso assíncrono."""

    def __init__(self) -> None:
        self._value: int = 0
        self._lock = asyncio.Lock()

    async def tick(self) -> int:
        """Incrementa o relógio localmente e retorna o novo valor."""
        async with self._lock:
            self._value += 1
            lamport_clock_metric.set(self._value)
            return self._value

    async def update(self, received: int) -> int:
        """
        Atualiza o relógio com base num timestamp recebido externamente.
        Aplica a regra: local = max(local, received) + 1.
        """
        async with self._lock:
            self._value = max(self._value, received) + 1
            lamport_clock_metric.set(self._value)
            return self._value

    @property 
    def value(self) -> int:
        """Leitura não-bloqueante do valor atual (apenas para observabilidade)."""
        return self._value


# Instância global — importada diretamente pelos serviços
lamport_clock = LamportClock()
