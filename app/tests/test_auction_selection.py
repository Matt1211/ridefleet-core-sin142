"""
Testes unitários para a função pura selecionar_vencedor.

Cobre os critérios de desempate determinístico:
  1. Menor preço
  2. Menor ETA (desempate em preço)
  3. group_id em ordem alfabética (desempate em preço e ETA)

Não usa banco de dados, RabbitMQ nem cliente HTTP.
"""

import pytest

from app.models.ride_proposal import RideProposal
from app.workers.auction_worker import selecionar_vencedor


def _proposta(group_id: str, price: float | None, eta: int | None) -> RideProposal:
    return RideProposal(
        group_id=group_id,
        service_url=f"http://{group_id}:8080",
        status="accepted",
        estimated_price=price,
        estimated_eta=eta,
    )


# ---------------------------------------------------------------------------
# Casos base
# ---------------------------------------------------------------------------


def test_lista_vazia_retorna_none():
    assert selecionar_vencedor([]) is None


def test_unica_proposta_vence():
    p = _proposta("alpha", price=100.0, eta=10)
    assert selecionar_vencedor([p]) is p


# ---------------------------------------------------------------------------
# Critério 1 — menor preço
# ---------------------------------------------------------------------------


def test_menor_preco_vence():
    barato = _proposta("alpha", price=50.0, eta=10)
    caro = _proposta("beta", price=100.0, eta=5)
    assert selecionar_vencedor([caro, barato]) is barato


def test_menor_preco_vence_independente_da_ordem():
    barato = _proposta("zulu", price=30.0, eta=20)
    caro = _proposta("alpha", price=80.0, eta=1)
    assert selecionar_vencedor([barato, caro]) is barato


# ---------------------------------------------------------------------------
# Critério 2 — menor ETA (empate em preço)
# ---------------------------------------------------------------------------


def test_empate_preco_menor_eta_vence():
    rapido = _proposta("alpha", price=100.0, eta=5)
    lento = _proposta("beta", price=100.0, eta=20)
    assert selecionar_vencedor([lento, rapido]) is rapido


def test_empate_preco_menor_eta_vence_tres_propostas():
    p1 = _proposta("alpha", price=100.0, eta=15)
    p2 = _proposta("beta", price=100.0, eta=5)
    p3 = _proposta("gamma", price=100.0, eta=10)
    assert selecionar_vencedor([p1, p2, p3]) is p2


# ---------------------------------------------------------------------------
# Critério 3 — group_id alfabético (empate em preço e ETA)
# ---------------------------------------------------------------------------


def test_empate_preco_e_eta_group_id_alfabetico():
    p_zulu = _proposta("zulu", price=100.0, eta=10)
    p_alpha = _proposta("alpha", price=100.0, eta=10)
    p_bravo = _proposta("bravo", price=100.0, eta=10)
    assert selecionar_vencedor([p_zulu, p_alpha, p_bravo]) is p_alpha


def test_empate_total_dois_grupos_menor_alphabetico():
    p_b = _proposta("grupo-b", price=75.0, eta=8)
    p_a = _proposta("grupo-a", price=75.0, eta=8)
    assert selecionar_vencedor([p_b, p_a]) is p_a


# ---------------------------------------------------------------------------
# Tratamento de None em preço/ETA
# ---------------------------------------------------------------------------


def test_preco_none_perde_para_preco_valido():
    sem_preco = _proposta("alpha", price=None, eta=1)
    com_preco = _proposta("beta", price=50.0, eta=100)
    assert selecionar_vencedor([sem_preco, com_preco]) is com_preco


def test_eta_none_perde_para_eta_valido_com_preco_igual():
    sem_eta = _proposta("alpha", price=100.0, eta=None)
    com_eta = _proposta("beta", price=100.0, eta=5)
    assert selecionar_vencedor([sem_eta, com_eta]) is com_eta


def test_todos_com_preco_e_eta_none_cai_no_group_id():
    p_z = _proposta("zulu", price=None, eta=None)
    p_a = _proposta("alfa", price=None, eta=None)
    assert selecionar_vencedor([p_z, p_a]) is p_a
