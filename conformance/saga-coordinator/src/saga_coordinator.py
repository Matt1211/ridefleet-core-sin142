"""
Saga Coordinator — conformizador da máquina de estados de corridas.

Valida todas as transições de estado contra a máquina de estados definida em
spec/saga/state-machine.md e aplica as regras de conformização do core.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Transições válidas: estado_atual -> conjunto de próximos estados permitidos
VALID_TRANSITIONS: dict[str, set[str]] = {
    "request":      {"match", "compensating", "cancelled"},
    "match":        {"confirm", "compensating", "cancelled"},
    "confirm":      {"in_transit", "compensating", "cancelled"},
    "in_transit":   {"complete", "compensating", "cancelled"},
    "complete":     set(),          # estado terminal
    "compensating": {"cancelled"},
    "cancelled":    set(),          # estado terminal
}

# Estados que exigem que o solicitante detenha o lock
LOCK_REQUIRED_TRANSITIONS: set[str] = {"confirm", "in_transit", "complete"}

# Estados terminais (não aceitam mais transições)
TERMINAL_STATES: set[str] = {"complete", "cancelled"}


@dataclass
class TransitionResult:
    accepted: bool
    error: Optional[str] = None


@dataclass
class SagaState:
    ride_id: str
    state: str = "request"
    assigned_service_id: Optional[str] = None
    logical_timestamp: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SagaCoordinator:
    """
    Conformizador da máquina de estados da saga de corridas.

    Regras aplicadas:
    1. Transição deve estar na tabela VALID_TRANSITIONS.
    2. Timestamp lógico deve ser maior que o último registrado.
    3. Para confirm/in_transit/complete: solicitante deve deter o lock.
    4. Estados terminais (complete/cancelled) não aceitam transições.
    """

    def validate_transition(
        self,
        current_state: str,
        new_state: str,
        service_id: str,
        logical_timestamp: int,
        last_logical_timestamp: int,
        lock_holder: Optional[str],
    ) -> TransitionResult:
        """
        Valida se uma transição é permitida.

        Args:
            current_state: Estado atual da corrida.
            new_state: Estado destino solicitado.
            service_id: Serviço que solicita a transição.
            logical_timestamp: Timestamp lógico do solicitante.
            last_logical_timestamp: Último timestamp lógico registrado para a corrida.
            lock_holder: service_id que detém o lock (None se lock livre).

        Returns:
            TransitionResult com accepted=True ou accepted=False + motivo.
        """
        # Regra 4: estado terminal
        if current_state in TERMINAL_STATES:
            return TransitionResult(
                accepted=False,
                error=f"Estado '{current_state}' é terminal — nenhuma transição aceita",
            )

        # Regra 1: transição válida
        allowed = VALID_TRANSITIONS.get(current_state, set())
        if new_state not in allowed:
            return TransitionResult(
                accepted=False,
                error=(
                    f"Transição '{current_state}' → '{new_state}' inválida. "
                    f"Permitidas: {sorted(allowed) or 'nenhuma'}"
                ),
            )

        # Regra 2: timestamp lógico crescente
        if logical_timestamp <= last_logical_timestamp:
            return TransitionResult(
                accepted=False,
                error=(
                    f"logicalTimestamp {logical_timestamp} deve ser maior que "
                    f"o último registrado ({last_logical_timestamp})"
                ),
            )

        # Regra 3: lock obrigatório para certas transições
        if new_state in LOCK_REQUIRED_TRANSITIONS:
            if lock_holder is None:
                return TransitionResult(
                    accepted=False,
                    error=f"Transição para '{new_state}' requer lock ativo",
                )
            if lock_holder != service_id:
                return TransitionResult(
                    accepted=False,
                    error=(
                        f"Transição para '{new_state}' requer que '{service_id}' "
                        f"detenha o lock (atual: '{lock_holder}')"
                    ),
                )

        return TransitionResult(accepted=True)

    def apply_transition(
        self,
        saga: SagaState,
        new_state: str,
        service_id: str,
        logical_timestamp: int,
        lock_holder: Optional[str],
    ) -> TransitionResult:
        """
        Valida e aplica a transição ao objeto SagaState.

        Mutação in-place em `saga` se aceita.
        """
        result = self.validate_transition(
            current_state=saga.state,
            new_state=new_state,
            service_id=service_id,
            logical_timestamp=logical_timestamp,
            last_logical_timestamp=saga.logical_timestamp,
            lock_holder=lock_holder,
        )

        if result.accepted:
            saga.state = new_state
            saga.logical_timestamp = logical_timestamp
            saga.updated_at = datetime.now(timezone.utc)
            if new_state == "match":
                saga.assigned_service_id = service_id

        return result

    def can_force_compensate(self, saga: SagaState) -> bool:
        """
        Retorna True se o core pode forçar transição para 'compensating'.
        (Qualquer estado não-terminal, exceto o próprio 'compensating'.)
        """
        return saga.state not in TERMINAL_STATES and saga.state != "compensating"
