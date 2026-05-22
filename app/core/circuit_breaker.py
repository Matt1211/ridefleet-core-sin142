from enum import Enum
import time

from app.core.metrics import circuit_breaker_metric

class CircuitBreakerState(int, Enum):
    '''Maquina de estados do circuit breaker'''
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2

class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout: int, service_id: str):
        self.service_id = service_id
        self._fail_counter: int = 0
        self._state = CircuitBreakerState.CLOSED
        self._last_time_opened = 0.0

        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

    @property
    def state(self) -> CircuitBreakerState:
        '''Checa e atualiza o estado do circuit breaker quando é chamado pelo check_state (Lazy Evaluation)'''
        if self._state == CircuitBreakerState.OPEN:
            time_gap = time.time() - self._last_time_opened
            if time_gap >= self._recovery_timeout:
                self._state = CircuitBreakerState.HALF_OPEN
                circuit_breaker_metric.labels(service=self.service_id).set(self._state.value)
        return self._state

    def fail_increment(self):
        '''Incrementa o contador de falhas e verifica se ultrapassou o limite'''
        self._fail_counter += 1

        if (self._fail_counter > self._failure_threshold and self._state == CircuitBreakerState.CLOSED) or self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            circuit_breaker_metric.labels(service=self.service_id).set(self._state.value)
            self._last_time_opened = time.time()

    def check_state(self) -> bool:
        '''Verifica o estado do breaker'''
        # Atualiza estado 
        estado_atual = self.state

        # Só permite executar se estiver CLOSED ou HALF_OPEN
        return estado_atual in (CircuitBreakerState.CLOSED, CircuitBreakerState.HALF_OPEN)

    def success(self):
        '''Reseta contadores de falha e atualiza estado para CLOSED'''
        self._fail_counter = 0
        self._state = CircuitBreakerState.CLOSED
        circuit_breaker_metric.labels(service=self.service_id).set(self._state.value)
