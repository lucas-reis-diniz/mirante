"""Regra: doação circular.

Constrói um grafo dirigido sobre a base inteira:

    doador  --doou-->  campanha  --pagou-->  fornecedor

Quando doador e fornecedor são a mesma entidade em algum ponto da cadeia, o
dinheiro saiu de uma campanha e voltou para a mesma cadeia. Isso é um ciclo.

Algoritmo: Tarjan para achar componentes fortemente conexas (um ciclo só pode
existir dentro de uma SCC), depois DFS com profundidade limitada dentro de
cada SCC para enumerar os ciclos concretos. Rodar DFS no grafo inteiro seria
inviável; a SCC reduz o espaço de busca em ordens de grandeza.

De novo: ciclo não é crime. Pode ser coligação, ressarcimento legítimo,
fornecedor que também é militante e doou. É indício que merece olho humano.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass

from .base import Actor, Evidence, RuleRun, Signal, brl

VERSION = "1.0"
RULE = "circular_donations"

MAX_DEPTH = 5
MIN_CYCLE_AMOUNT_CENTS = 1_000_000  # R$ 10.000,00 movimentados no ciclo
HIGH_MAX_NODES = 3                  # ciclo curto é mais difícil de explicar


@dataclass(frozen=True)
class Node:
    """Um vértice do grafo. kind separa namespaces de id."""

    kind: str   # 'org' (campanha) | 'doc' (CPF/CNPJ de doador ou fornecedor)
    key: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}"


EDGES_DONATION_SQL = """
SELECT d.donor_cpf_cnpj AS doc, o.id AS org_id,
       sum(d.amount_cents) AS total_cents, count(*) AS n,
       min(d.id) AS sample_id, min(d.provenance_id) AS provenance_id,
       max(d.donor_name) AS doc_name
FROM campaign_donation d
JOIN campaign_org o ON o.id = d.campaign_org_id
WHERE d.donor_cpf_cnpj IS NOT NULL AND d.amount_cents > 0
GROUP BY d.donor_cpf_cnpj, o.id
"""

EDGES_EXPENSE_SQL = """
SELECT e.supplier_cpf_cnpj AS doc, o.id AS org_id,
       sum(e.amount_cents) AS total_cents, count(*) AS n,
       min(e.id) AS sample_id, min(e.provenance_id) AS provenance_id,
       max(e.supplier_name) AS doc_name
FROM campaign_expense e
JOIN campaign_org o ON o.id = e.campaign_org_id
WHERE e.supplier_cpf_cnpj IS NOT NULL AND e.amount_cents > 0
GROUP BY e.supplier_cpf_cnpj, o.id
"""


class Graph:
    def __init__(self) -> None:
        self.adj: dict[Node, set[Node]] = defaultdict(set)
        self.edge_meta: dict[tuple[Node, Node], dict] = {}
        self.names: dict[str, str] = {}

    def add(self, src: Node, dst: Node, meta: dict) -> None:
        self.adj[src].add(dst)
        self.adj.setdefault(dst, set())
        self.edge_meta[(src, dst)] = meta

    @property
    def node_count(self) -> int:
        return len(self.adj)

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self.adj.values())


def build_graph(conn) -> Graph:
    graph = Graph()

    with conn.cursor() as cur:
        cur.execute(EDGES_DONATION_SQL)
        for row in cur:
            src = Node("doc", row["doc"])
            dst = Node("org", str(row["org_id"]))
            graph.add(src, dst, {
                "kind": "donation",
                "total_cents": row["total_cents"],
                "count": row["n"],
                "table": "campaign_donation",
                "row_id": row["sample_id"],
                "provenance_id": row["provenance_id"],
            })
            if row["doc_name"]:
                graph.names[row["doc"]] = row["doc_name"]

        cur.execute(EDGES_EXPENSE_SQL)
        for row in cur:
            src = Node("org", str(row["org_id"]))
            dst = Node("doc", row["doc"])
            graph.add(src, dst, {
                "kind": "expense",
                "total_cents": row["total_cents"],
                "count": row["n"],
                "table": "campaign_expense",
                "row_id": row["sample_id"],
                "provenance_id": row["provenance_id"],
            })
            if row["doc_name"]:
                graph.names.setdefault(row["doc"], row["doc_name"])

    return graph


def tarjan_scc(graph: Graph) -> list[list[Node]]:
    """Componentes fortemente conexas, iterativo (recursão estoura em 5M nós)."""
    index_of: dict[Node, int] = {}
    low: dict[Node, int] = {}
    on_stack: set[Node] = set()
    stack: list[Node] = []
    result: list[list[Node]] = []
    counter = 0

    for root in list(graph.adj):
        if root in index_of:
            continue

        work: list[tuple[Node, int]] = [(root, 0)]
        while work:
            node, child_idx = work[-1]

            if child_idx == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)

            children = sorted(graph.adj[node], key=str)
            if child_idx < len(children):
                work[-1] = (node, child_idx + 1)
                child = children[child_idx]
                if child not in index_of:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index_of[node]:
                    component: list[Node] = []
                    while True:
                        popped = stack.pop()
                        on_stack.discard(popped)
                        component.append(popped)
                        if popped == node:
                            break
                    if len(component) > 1:
                        result.append(component)

    return result


def find_cycles(graph: Graph, component: list[Node], max_depth: int) -> list[list[Node]]:
    """Enumera ciclos dentro de uma SCC, com profundidade limitada.

    Limitar a profundidade é escolha deliberada: ciclos longos são quase
    sempre coincidência estrutural (todo mundo compra do mesmo gráfico),
    enquanto ciclos curtos são os que valem apuração.
    """
    members = set(component)
    cycles: list[list[Node]] = []
    seen: set[tuple[Node, ...]] = set()

    for start in component:
        path: list[Node] = [start]
        in_path = {start}

        def dfs(node: Node, depth: int) -> None:
            if depth >= max_depth:
                return
            for neighbor in graph.adj[node]:
                if neighbor not in members:
                    continue
                if neighbor == start and len(path) > 1:
                    canonical = _canonical(path)
                    if canonical not in seen:
                        seen.add(canonical)
                        cycles.append(list(path))
                elif neighbor not in in_path:
                    path.append(neighbor)
                    in_path.add(neighbor)
                    dfs(neighbor, depth + 1)
                    path.pop()
                    in_path.discard(neighbor)

        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, max_depth * 10 + 100))
        try:
            dfs(start, 0)
        finally:
            sys.setrecursionlimit(old_limit)

    return cycles


def _canonical(path: list[Node]) -> tuple[Node, ...]:
    """Rotação canônica: o mesmo ciclo visto de pontos diferentes é um só."""
    if not path:
        return ()
    pivot = min(range(len(path)), key=lambda i: str(path[i]))
    return tuple(path[pivot:] + path[:pivot])


def run(conn, max_depth: int = MAX_DEPTH, min_amount: int = MIN_CYCLE_AMOUNT_CENTS) -> dict:
    print("  montando grafo do dinheiro...")
    graph = build_graph(conn)
    print(f"  grafo: {graph.node_count:,} nós / {graph.edge_count:,} arestas")

    print("  procurando componentes fortemente conexas (Tarjan)...")
    components = tarjan_scc(graph)
    print(f"  {len(components):,} componentes com mais de um nó")

    summary = {
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "components": len(components),
        "cycles_found": 0,
        "signals": 0,
    }

    with RuleRun(
        conn, RULE, VERSION,
        params={"max_depth": max_depth, "min_amount_cents": min_amount},
    ) as run_ctx:
        run_ctx.rows_scanned = graph.edge_count

        for component in components:
            for cycle in find_cycles(graph, component, max_depth):
                summary["cycles_found"] += 1

                edges = list(zip(cycle, cycle[1:] + cycle[:1]))
                metas = [graph.edge_meta[e] for e in edges if e in graph.edge_meta]
                if not metas:
                    continue

                # O valor do ciclo é o gargalo: o máximo que pode ter
                # efetivamente circulado é o menor elo da corrente.
                total = min(m["total_cents"] for m in metas)
                if total < min_amount:
                    continue

                severity = "high" if len(cycle) <= HIGH_MAX_NODES else "medium"

                actors = []
                for node in cycle:
                    if node.kind == "doc":
                        actors.append(
                            Actor(
                                role="intermediary",
                                display_name=graph.names.get(node.key, node.key),
                            )
                        )

                run_ctx.emit(
                    Signal(
                        severity=severity,
                        headline=(
                            f"Ciclo de {len(cycle)} nós movimentando ao menos "
                            f"{brl(total)} entre doação e pagamento de campanha"
                        ),
                        amount_cents=total,
                        detail={
                            "cycle": [str(n) for n in cycle],
                            "path_length": len(cycle),
                            "bottleneck_cents": total,
                            "edges": [
                                {"kind": m["kind"], "total_cents": m["total_cents"], "count": m["count"]}
                                for m in metas
                            ],
                            "caveat": (
                                "Ciclo pode decorrer de coligação, ressarcimento "
                                "ou fornecedor que também doou. Indício, não prova."
                            ),
                        },
                        actors=actors[:8],
                        evidence=[
                            Evidence(
                                table_name=m["table"],
                                row_id=m["row_id"],
                                provenance_id=m["provenance_id"],
                                note=f"aresta {m['kind']} do ciclo",
                            )
                            for m in metas
                        ],
                    )
                )
                summary["signals"] += 1

    print(f"  {summary['cycles_found']:,} ciclos; {summary['signals']:,} viraram sinal")
    return summary
