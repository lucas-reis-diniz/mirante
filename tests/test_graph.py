"""Testes da detecção de ciclos. Grafo montado à mão, sem banco.

A regra de doação circular é a mais fácil de errar em silêncio: um bug no
Tarjan não levanta exceção, só devolve ciclos a menos. Então testamos contra
grafos cujos ciclos sabemos de cor.
"""

from mirante.rules.circular_donations import Graph, Node, find_cycles, tarjan_scc


def edge_meta(kind: str, cents: int) -> dict:
    return {
        "kind": kind,
        "total_cents": cents,
        "count": 1,
        "table": "campaign_donation" if kind == "donation" else "campaign_expense",
        "row_id": 1,
        "provenance_id": 1,
    }


def build(edges: list[tuple[str, str, str, int]]) -> Graph:
    graph = Graph()
    for src, dst, kind, cents in edges:
        s_kind, s_key = src.split(":")
        d_kind, d_key = dst.split(":")
        graph.add(Node(s_kind, s_key), Node(d_kind, d_key), edge_meta(kind, cents))
    return graph


class TestTarjan:
    def test_grafo_sem_ciclo_nao_tem_scc(self):
        # doador -> campanha -> fornecedor diferente: fluxo normal
        graph = build([
            ("doc:AAA", "org:1", "donation", 10_000),
            ("org:1", "doc:BBB", "expense", 8_000),
        ])
        assert tarjan_scc(graph) == []

    def test_ciclo_simples_vira_scc(self):
        # mesma entidade doa e recebe: o caso que a regra existe para achar
        graph = build([
            ("doc:AAA", "org:1", "donation", 50_000),
            ("org:1", "doc:AAA", "expense", 40_000),
        ])
        components = tarjan_scc(graph)
        assert len(components) == 1
        assert set(components[0]) == {Node("doc", "AAA"), Node("org", "1")}

    def test_duas_sccs_independentes(self):
        graph = build([
            ("doc:AAA", "org:1", "donation", 10_000),
            ("org:1", "doc:AAA", "expense", 10_000),
            ("doc:BBB", "org:2", "donation", 20_000),
            ("org:2", "doc:BBB", "expense", 20_000),
        ])
        assert len(tarjan_scc(graph)) == 2

    def test_nao_estoura_pilha_em_cadeia_longa(self):
        # Recursão ingênua morre aqui. Tarjan iterativo não.
        edges = [(f"org:{i}", f"org:{i + 1}", "expense", 100) for i in range(5_000)]
        edges.append(("org:5000", "org:0", "donation", 100))
        graph = build(edges)
        components = tarjan_scc(graph)
        assert len(components) == 1
        assert len(components[0]) == 5_001


class TestCycleEnumeration:
    def test_encontra_ciclo_de_dois_nos(self):
        graph = build([
            ("doc:AAA", "org:1", "donation", 50_000),
            ("org:1", "doc:AAA", "expense", 40_000),
        ])
        cycles = find_cycles(graph, tarjan_scc(graph)[0], max_depth=5)
        assert len(cycles) == 1
        assert len(cycles[0]) == 2

    def test_mesmo_ciclo_nao_conta_duas_vezes(self):
        # O triângulo A->B->C->A é um ciclo só, visto de três pontos.
        graph = build([
            ("doc:AAA", "org:1", "donation", 10_000),
            ("org:1", "doc:BBB", "expense", 10_000),
            ("doc:BBB", "org:1", "donation", 10_000),
        ])
        cycles = find_cycles(graph, tarjan_scc(graph)[0], max_depth=5)
        keys = {tuple(sorted(str(n) for n in c)) for c in cycles}
        assert len(cycles) == len(keys)

    def test_profundidade_limita_a_busca(self):
        edges = [(f"org:{i}", f"org:{i + 1}", "expense", 100) for i in range(9)]
        edges.append(("org:9", "org:0", "donation", 100))
        graph = build(edges)
        component = tarjan_scc(graph)[0]

        assert find_cycles(graph, component, max_depth=3) == []
        assert len(find_cycles(graph, component, max_depth=12)) == 1


class TestBottleneck:
    def test_valor_do_ciclo_e_o_menor_elo(self):
        # Se entraram 50k mas só saíram 5k, no máximo 5k circulou.
        graph = build([
            ("doc:AAA", "org:1", "donation", 5_000_000),
            ("org:1", "doc:AAA", "expense", 500_000),
        ])
        cycle = find_cycles(graph, tarjan_scc(graph)[0], max_depth=5)[0]
        edges = list(zip(cycle, cycle[1:] + cycle[:1]))
        metas = [graph.edge_meta[e] for e in edges if e in graph.edge_meta]
        assert min(m["total_cents"] for m in metas) == 500_000
