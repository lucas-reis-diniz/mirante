/**
 * Formatação para leitura. O banco guarda dinheiro em centavos como bigint,
 * e o driver devolve bigint como string para não perder precisão — então a
 * conversão acontece aqui, uma vez, e nunca com `parseFloat` sobre centavos.
 */

export function brl(cents: string | number | null | undefined): string {
  if (cents === null || cents === undefined || cents === "") return "—";
  const value = typeof cents === "string" ? BigInt(cents) : BigInt(Math.round(cents));
  const negative = value < 0n;
  const abs = negative ? -value : value;
  const reais = abs / 100n;
  const centavos = abs % 100n;
  const formatted = reais.toLocaleString("pt-BR");
  return `${negative ? "-" : ""}R$ ${formatted},${centavos.toString().padStart(2, "0")}`;
}

export function count(n: string | number | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  return Number(n).toLocaleString("pt-BR");
}

export function date(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function dateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("pt-BR", { timeZone: "UTC" })} ${d.toLocaleTimeString(
    "pt-BR",
    { hour: "2-digit", minute: "2-digit", timeZone: "UTC" },
  )} UTC`;
}

export function cnpj(value: string | null | undefined): string {
  if (!value || value.length !== 14) return value ?? "—";
  return value.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5");
}

/** Rótulos em português para os nomes internos das regras. */
export const RULE_LABELS: Record<string, string> = {
  disproportionate_expense: "Despesa desproporcional",
  circular_donations: "Doação circular",
  sanctioned_counterparty: "Contraparte sancionada",
};

export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? rule;
}

export const SEVERITY_LABELS: Record<string, string> = {
  high: "alta",
  medium: "média",
  low: "baixa",
};
