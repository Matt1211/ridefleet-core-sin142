from typing import Dict
from circuit_breaker import *

failure_threshold = 2 # temporario

recovery_timeout = 5

class CircuitBreakerManager:
    '''Gerenciador de circuit breakers dos serviços'''
    def __init__(self, failure_threshold: int, recovery_timeout: int):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

    def get_breaker(self, group_id: str) -> CircuitBreaker:
        '''Aquisição/adição do grupo no dicionario de breakers'''
        if group_id not in self._breakers:
            self._breakers[group_id] = CircuitBreaker(self._failure_threshold, self._recovery_timeout)
        return self._breakers[group_id]