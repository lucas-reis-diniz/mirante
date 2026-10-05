"""CLI do Mirante.

    mirante init-db
    mirante purge-demo
    mirante tse-candidates --years 2022 2026 --uf SP
    mirante tse-accounts --years 2022
    mirante camara-ceap --years 2025 2026
    mirante camara-votes --years 2025 2026
    mirante transparencia-sanctions
    mirante rule-disproportionate-expense
    mirante rule-circular-donations
    mirante rule-sanctioned-counterparty
    mirante status
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .db.connection import ConfigError, connect, init_db, table_counts

REPORT_DIR = Path(".")


def _write_report(name: str, payload: dict) -> None:
    """Relatório por execução, ao lado do manifesto. É log auditável."""
    path = REPORT_DIR / f"{name}_report.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nrelatório: {path}")


def cmd_init_db(args: argparse.Namespace) -> int:
    created = init_db()
    print("schema aplicado:")
    for item in created:
        print(f"  {item}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    counts = table_counts()
    if not counts:
        print("banco vazio ou schema não aplicado. Rode: mirante init-db")
        return 1

    width = max(len(k) for k in counts)
    print("tabela".ljust(width), "linhas (aprox.)".rjust(18))
    print("-" * (width + 19))
    for table, rows in counts.items():
        if rows:
            print(table.ljust(width), f"{rows:,}".rjust(18))

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT crawler, max(started_at) AS last_run, count(*) AS runs
            FROM collection WHERE status = 'ok' GROUP BY crawler ORDER BY crawler
            """
        )
        runs = cur.fetchall()
        if runs:
            print("\núltima coleta por crawler:")
            for r in runs:
                print(f"  {r['crawler']:32s} {r['last_run']:%Y-%m-%d %H:%M} UTC  ({r['runs']} execuções)")

        cur.execute(
            """
            SELECT rule, severity, count(*) AS n, sum(amount_cents) AS total
            FROM signal GROUP BY rule, severity ORDER BY rule, severity
            """
        )
        signals = cur.fetchall()
        if signals:
            print("\nsinais de alerta (indício, não prova):")
            for s in signals:
                total = f"R$ {(s['total'] or 0) / 100:,.0f}"
                print(f"  {s['rule']:30s} {s['severity']:7s} {s['n']:>8,}  {total:>20s}")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from . import seed_demo

    with connect() as conn:
        report = seed_demo.run(conn)
    print("\ndados SINTÉTICOS carregados — não correspondem a pessoa ou empresa real:")
    for k, v in report["counts"].items():
        print(f"  {k:16s} {v:>6,}")
    print(
        "\nRode as regras para ver os sinais:\n"
        "  mirante rule-disproportionate-expense\n"
        "  mirante rule-sanctioned-counterparty\n"
        "  mirante rule-circular-donations"
    )
    return 0


def cmd_purge_demo(args: argparse.Namespace) -> int:
    from . import seed_demo

    with connect() as conn:
        removed = seed_demo.purge(conn)
    print("dados de demonstração apagados" if removed else "sem dados de demonstração no banco")
    return 0


def cmd_tse_candidates(args: argparse.Namespace) -> int:
    from .sources import tse_candidates

    with connect() as conn:
        report = tse_candidates.run(conn, years=args.years, uf=args.uf)
    _write_report("tse_candidates", report)
    return 0


def cmd_tse_accounts(args: argparse.Namespace) -> int:
    from .sources import tse_accounts

    with connect() as conn:
        report = tse_accounts.run(conn, years=args.years, uf=args.uf)
    _write_report("tse_accounts", report)
    return 0


def cmd_camara_ceap(args: argparse.Namespace) -> int:
    from .sources import camara_ceap

    with connect() as conn:
        report = camara_ceap.run(conn, years=args.years)
    _write_report("camara_ceap", report)
    return 0


def cmd_camara_votes(args: argparse.Namespace) -> int:
    from .sources import camara_votes

    with connect() as conn:
        report = camara_votes.run(conn, years=args.years)
    _write_report("camara_votes", report)
    return 0


def cmd_transparencia_sanctions(args: argparse.Namespace) -> int:
    from .sources import transparencia_sanctions

    snapshot = date.fromisoformat(args.snapshot) if args.snapshot else None
    with connect() as conn:
        report = transparencia_sanctions.run(conn, snapshot=snapshot)
    _write_report("transparencia_sanctions", report)
    return 0


def cmd_rule_disproportionate(args: argparse.Namespace) -> int:
    from .rules import disproportionate_expense

    with connect() as conn:
        report = disproportionate_expense.run(conn)
    _write_report("rule_disproportionate_expense", report)
    return 0


def cmd_rule_circular(args: argparse.Namespace) -> int:
    from .rules import circular_donations

    with connect() as conn:
        report = circular_donations.run(
            conn, max_depth=args.max_depth, min_amount=args.min_amount
        )
    _write_report("rule_circular_donations", report)
    return 0


def cmd_rule_sanctioned(args: argparse.Namespace) -> int:
    from .rules import sanctioned_counterparty

    with connect() as conn:
        report = sanctioned_counterparty.run(conn)
    _write_report("rule_sanctioned_counterparty", report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mirante",
        description="Cruzamento de dados públicos sobre política brasileira. "
        "Gera indícios, nunca conclusões.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="cria extensões e schema").set_defaults(func=cmd_init_db)
    sub.add_parser("status", help="o que já foi coletado").set_defaults(func=cmd_status)
    sub.add_parser(
        "seed-demo",
        help="dados SINTÉTICOS para desenvolver a interface sem baixar o TSE",
    ).set_defaults(func=cmd_seed_demo)
    sub.add_parser(
        "purge-demo",
        help="apaga os dados sintéticos, se houver — rodar antes da primeira coleta real",
    ).set_defaults(func=cmd_purge_demo)

    p = sub.add_parser("tse-candidates", help="candidaturas 2014-2026 (TSE consulta_cand)")
    p.add_argument("--years", nargs="+", type=int)
    p.add_argument("--uf", help="limita a uma UF — útil para a primeira rodada")
    p.set_defaults(func=cmd_tse_candidates)

    p = sub.add_parser("tse-accounts", help="CNPJ de campanha, doações e despesas")
    p.add_argument("--years", nargs="+", type=int)
    p.add_argument("--uf")
    p.set_defaults(func=cmd_tse_accounts)

    p = sub.add_parser("camara-ceap", help="cota parlamentar da Câmara")
    p.add_argument("--years", nargs="+", type=int)
    p.set_defaults(func=cmd_camara_ceap)

    p = sub.add_parser("camara-votes", help="votações da Câmara e o voto de cada deputado")
    p.add_argument("--years", nargs="+", type=int)
    p.set_defaults(func=cmd_camara_votes)

    p = sub.add_parser("transparencia-sanctions", help="CEIS e CNEP (snapshot)")
    p.add_argument("--snapshot", help="AAAA-MM-DD; padrão: hoje, andando para trás se faltar")
    p.set_defaults(func=cmd_transparencia_sanctions)

    sub.add_parser(
        "rule-disproportionate-expense", help="item barato com valor fora da curva"
    ).set_defaults(func=cmd_rule_disproportionate)

    p = sub.add_parser("rule-circular-donations", help="ciclos de dinheiro no grafo")
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--min-amount", type=int, default=1_000_000, help="em centavos")
    p.set_defaults(func=cmd_rule_circular)

    sub.add_parser(
        "rule-sanctioned-counterparty", help="empresa sancionada que recebeu dinheiro"
    ).set_defaults(func=cmd_rule_sanctioned)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrompido", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
