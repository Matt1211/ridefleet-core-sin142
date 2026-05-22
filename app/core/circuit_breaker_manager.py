from typing import Dict
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerState, circuit_breaker_metric

# .env.example não está presente na infra, incluir as variaveis de ambiente a seguir e importa-las aqui e no ride controller
FAILURE_THRESHOLD = 2 # temporario

RECOVERY_TIMEOUT = 20

class CircuitBreakerManager:
    '''Gerenciador de circuit breakers dos serviços'''
    def __init__(self, failure_threshold: int, recovery_timeout: int):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

    def get_breaker(self, group_id: str) -> CircuitBreaker:
        '''Aquisição/adição do grupo no dicionario de breakers'''
        if group_id not in self._breakers:
            self._breakers[group_id] = CircuitBreaker(self._failure_threshold, self._recovery_timeout, group_id)
            circuit_breaker_metric.labels(service=group_id).set(0)
            
        return self._breakers[group_id]
    
# Instancia global do circuit breaker manager
circuit_breaker_manager = CircuitBreakerManager(FAILURE_THRESHOLD, RECOVERY_TIMEOUT)