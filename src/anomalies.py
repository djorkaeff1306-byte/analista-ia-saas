"""
Detector de anomalías (fase 3).

Flujo:
    1. Calcula 4 tasas mensuales (fallo de pagos, churn voluntario, churn
       involuntario, conversión trial -> pago) para el total y por país,
       canal y plan.
    2. Dos reglas, ambas con |z| >= 3:
         - pico:             el mes contra los 6 meses anteriores. Como los
                             segmentos son chicos, la desviación estándar
                             tiene un piso binomial: una variación esperable
                             por tamaño de muestra no se marca.
         - cambio de nivel:  los últimos 3 meses juntos contra los 6
                             anteriores (test de dos proporciones). Detecta
                             caídas lentas que mes a mes quedan dentro del
                             ruido. Solo avisa el primer mes en que se cruza.
       Además se exige una diferencia de al menos 5 eventos (clientes,
       cobros o registros) respecto de lo esperado: menos no es accionable.
    3. Claude redacta un resumen del periodo con hipótesis de causa.

El grano por defecto es mensual (y no semanal) porque por semana los segmentos
tienen muy pocos eventos: con ~13 cobros semanales en un país, el ruido tapa la
señal. También se puede analizar un rango de días cualquiera (build_panel con
`period`): se compara contra los 6 rangos anteriores de igual duración, con las
mismas reglas. En rangos cortos habrá menos alertas, por el mismo motivo.

Uso:
    python src/anomalies.py                    # último mes, con resumen de Claude
    python src/anomalies.py --month 2026-03    # un mes específico
    python src/anomalies.py --month 2026-03 --no-llm
    python src/anomalies.py --all              # todas las alertas de todos los meses (sin LLM)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta

import anthropic
import duckdb
import numpy as np
import pandas as pd
import yaml

from agent import DB_PATH, FALLBACK_BETA, MODEL, OUTPUT_DIR, ROOT, SEMANTIC_LAYER_PATH, AgentError

# Los resúmenes se versionan: cuestan llamadas a la API y sirven de ejemplo en el repo
REPORTS_DIR = ROOT / "reports"

WINDOW = 6            # meses de base para comparar
RECENT = 3            # meses recientes que se agrupan en la regla "cambio de nivel"
Z_THRESHOLD = 3.0     # umbral de ambas reglas
MIN_BASE = 30         # denominador mínimo para evaluar una tasa en un mes
MIN_EXCESS = 5        # diferencia mínima de eventos vs lo esperado para alertar

# Dimensiones: nombre -> expresión SQL (companies = co, subscriptions = s)
DIMENSIONS = {
    "total": "'total'",
    "pais": "co.country_code",
    "canal": "co.acquisition_channel",
    "plan": "s.plan_id",
}


@dataclass(frozen=True)
class Metric:
    key: str            # clave en semantic_layer.yaml
    label: str
    sql: str            # plantilla con {dim}; devuelve month, segment, num, den
    dims: tuple[str, ...]
    bad_direction: int  # +1 si subir es malo, -1 si bajar es malo


# SQL derivado de las métricas de la capa semántica, abierto por segmento.
# Las tasas se calculan como num / den para poder aplicar el piso binomial.
METRICS = [
    Metric(
        key="payment_failure_rate",
        label="Tasa de fallo de pagos (1er intento)",
        sql="""
            SELECT c.month, {dim} AS segment,
                   SUM(CASE WHEN p.status = 'failed' THEN 1 ELSE 0 END) AS num,
                   COUNT(*) AS den
            FROM payments p
            JOIN subscriptions s ON s.subscription_id = p.subscription_id
            JOIN companies co ON co.company_id = p.company_id
            JOIN calendar_months c ON p.due_date BETWEEN c.month_start AND c.month_end
            WHERE p.attempt_number = 1
            GROUP BY 1, 2""",
        dims=("total", "pais", "canal", "plan"),
        bad_direction=+1,
    ),
    *[
        Metric(
            key=f"logo_churn_rate_{churn_type}",
            label=f"Churn {churn_type} de clientes",
            sql=f"""
                WITH base AS (
                  SELECT c.month, {{dim}} AS segment, COUNT(DISTINCT s.company_id) AS den
                  FROM calendar_months c
                  JOIN subscriptions s
                    ON s.start_date <= c.prev_month_end
                   AND (s.end_date IS NULL OR s.end_date > c.prev_month_end)
                  JOIN companies co ON co.company_id = s.company_id
                  GROUP BY 1, 2
                ),
                churned AS (
                  SELECT c.month, {{dim}} AS segment, COUNT(DISTINCT s.company_id) AS num
                  FROM calendar_months c
                  JOIN subscriptions s
                    ON s.status = 'churned' AND s.churn_type = '{churn_type}'
                   AND s.end_date BETWEEN c.month_start AND c.month_end
                  JOIN companies co ON co.company_id = s.company_id
                  GROUP BY 1, 2
                )
                SELECT b.month, b.segment, COALESCE(ch.num, 0) AS num, b.den
                FROM base b
                LEFT JOIN churned ch ON ch.month = b.month AND ch.segment = b.segment""",
            dims=("total", "pais", "canal", "plan"),
            bad_direction=+1,
        )
        for churn_type in ("voluntario", "involuntario")
    ],
    Metric(
        key="trial_to_paid_conversion",
        label="Conversión trial -> pago",
        # Solo cohortes cerradas: todos sus trials terminaron antes de la fecha de corte
        sql="""
            SELECT c.month, {dim} AS segment,
                   SUM(CASE WHEN EXISTS (
                         SELECT 1 FROM subscriptions s
                         WHERE s.company_id = co.company_id
                           AND s.previous_subscription_id IS NULL
                           AND s.start_date <= co.trial_end_date)
                       THEN 1 ELSE 0 END) AS num,
                   COUNT(*) AS den
            FROM companies co
            JOIN calendar_months c ON co.signup_date BETWEEN c.month_start AND c.month_end
            WHERE c.month_end + INTERVAL 15 DAY <= (SELECT MAX(month_end) FROM calendar_months)
            GROUP BY 1, 2""",
        dims=("total", "pais", "canal"),   # el plan no existe antes de convertir
        bad_direction=-1,
    ),
]
METRIC_LABELS = {m.key: m.label for m in METRICS}


# ---------------------------------------------------------------------
# 1. Panel de métricas
# ---------------------------------------------------------------------
def _over_windows(sql: str, start: date, end: date) -> str:
    """Adapta el SQL mensual a ventanas de días: la ventana pedida y las N anteriores de igual
    duración, contiguas. La tabla `windows` imita las columnas de calendar_months; en `month`
    va la fecha de inicio de cada ventana (ISO, así ordena bien)."""
    length = (end - start).days + 1
    rows = []
    for k in range(WINDOW + RECENT + 1):
        ws, we = start - timedelta(days=k * length), end - timedelta(days=k * length)
        rows.append(f"('{ws}', DATE '{ws}', DATE '{we}', DATE '{ws - timedelta(days=1)}')")
    cte = f"windows(month, month_start, month_end, prev_month_end) AS (VALUES {', '.join(rows)})"
    sql = sql.strip().replace("calendar_months c", "windows c")
    return f"WITH {cte}, {sql[4:]}" if sql.upper().startswith("WITH") else f"WITH {cte} {sql}"


def build_panel(con: duckdb.DuckDBPyConnection, period: tuple[date, date] | None = None) -> pd.DataFrame:
    """Una fila por métrica, dimensión, segmento y periodo, con num, den y rate.

    Sin `period`, los periodos son meses calendario. Con `period` (inicio, fin), son ventanas
    de días: la pedida y las anteriores de igual duración; la columna `month` guarda la
    fecha de inicio de cada ventana, así detect() funciona igual en ambos casos."""
    frames = []
    for m in METRICS:
        for dim in m.dims:
            sql = m.sql.format(dim=DIMENSIONS[dim])
            df = con.execute(_over_windows(sql, *period) if period else sql).df()
            df["metric"], df["dimension"] = m.key, dim
            frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    panel["num"] = panel["num"].astype(float)
    panel["den"] = panel["den"].astype(float)
    panel["rate"] = panel["num"] / panel["den"]
    return (panel[["metric", "dimension", "segment", "month", "num", "den", "rate"]]
            .sort_values(["metric", "dimension", "segment", "month"])
            .reset_index(drop=True))


# ---------------------------------------------------------------------
# 2. Detección
# ---------------------------------------------------------------------
def _pooled_rate(base: pd.DataFrame) -> float:
    """Tasa agregada de la base, suavizada para que nunca sea exactamente 0 o 1."""
    return (base["num"].sum() + 0.5) / (base["den"].sum() + 1)


def _spike_z(g: pd.DataFrame, i: int) -> tuple[float, float, float]:
    """Pico: mes i contra los WINDOW meses anteriores. Devuelve (z, base, exceso)."""
    row, base = g.loc[i], g.loc[i - WINDOW:i - 1]
    p = _pooled_rate(base)
    sample_std = base["rate"].std(ddof=1)
    binomial_std = np.sqrt(p * (1 - p) / row["den"])
    std = max(sample_std if np.isfinite(sample_std) else 0.0, binomial_std)
    return (row["rate"] - p) / std, p, row["num"] - p * row["den"]


def _level_shift_z(g: pd.DataFrame, i: int) -> tuple[float, float, float, float]:
    """Cambio de nivel: meses i-RECENT+1..i juntos contra los WINDOW anteriores.
    Test de dos proporciones. Devuelve (z, tasa reciente, base, exceso)."""
    recent = g.loc[i - RECENT + 1:i]
    base = g.loc[i - RECENT - WINDOW + 1:i - RECENT]
    n1, n0 = recent["den"].sum(), base["den"].sum()
    p1 = recent["num"].sum() / n1
    p0 = _pooled_rate(base)
    pooled = (recent["num"].sum() + base["num"].sum() + 0.5) / (n1 + n0 + 1)
    se = np.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n0))
    return (p1 - p0) / se, p1, p0, recent["num"].sum() - p0 * n1


def detect(panel: pd.DataFrame, months: list[str] | None = None) -> pd.DataFrame:
    """Devuelve las alertas de los meses pedidos (todos si months es None)."""
    bad_dir = {m.key: m.bad_direction for m in METRICS}
    alerts = []

    def add(row, rule, rate, baseline, z, num, den, excess):
        alerts.append({
            "month": row["month"], "metric": row["metric"], "dimension": row["dimension"],
            "segment": row["segment"], "rule": rule, "rate": rate, "baseline": baseline,
            "z": z, "num": num, "den": den,
            # Eventos por encima (o por debajo) de lo esperado según la base
            "excess": excess,
            "direction": "empeora" if np.sign(z) == bad_dir[row["metric"]] else "mejora",
        })

    def level_shift_fires(g, i) -> bool:
        if i < RECENT + WINDOW - 1 or (g.loc[i - RECENT + 1:i, "den"] < MIN_BASE).any():
            return False
        z, *_, excess = _level_shift_z(g, i)
        return abs(z) >= Z_THRESHOLD and abs(excess) >= MIN_EXCESS

    for _, g in panel.groupby(["metric", "dimension", "segment"], sort=False):
        g = g.reset_index(drop=True)
        for i in range(WINDOW, len(g)):
            row = g.loc[i]
            if (months is not None and row["month"] not in months) or row["den"] < MIN_BASE:
                continue

            z, base, excess = _spike_z(g, i)
            if abs(z) >= Z_THRESHOLD and abs(excess) >= MIN_EXCESS:
                add(row, "pico", row["rate"], base, z, row["num"], row["den"], excess)
            # Cambio de nivel: solo el primer mes en que se cruza el umbral
            elif level_shift_fires(g, i) and not level_shift_fires(g, i - 1):
                z, rate, base, excess = _level_shift_z(g, i)
                recent = g.loc[i - RECENT + 1:i]
                add(row, "cambio de nivel", rate, base, z,
                    recent["num"].sum(), recent["den"].sum(), excess)

    cols = ["month", "metric", "dimension", "segment", "rule", "rate", "baseline",
            "z", "num", "den", "excess", "direction"]
    out = pd.DataFrame(alerts, columns=cols)
    return (out.assign(abs_z=out["z"].abs())
               .sort_values(["month", "abs_z"], ascending=[True, False])
               .drop(columns="abs_z")
               .reset_index(drop=True))


# ---------------------------------------------------------------------
# 3. Resumen con Claude
# ---------------------------------------------------------------------
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Una frase con lo más importante del periodo."},
        "alerts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "alert_ids": {"type": "array", "items": {"type": "string"},
                                  "description": "IDs de las alertas del detector que agrupa; la primera es la principal."},
                    "severity": {"type": "string", "enum": ["alta", "media", "baja"]},
                    "what_happened": {"type": "string",
                                      "description": "Qué cambió, con cifras de las alertas y el contexto."},
                    "related_signals": {"type": "string",
                                        "description": "Otras alertas o series que se explican por esta, o ''."},
                    "hypotheses": {"type": "array", "items": {"type": "string"},
                                   "description": "1-3 hipótesis de causa, de la más a la menos probable."},
                    "next_check": {"type": "string",
                                   "description": "Qué consulta o dato permitiría confirmar la hipótesis principal."},
                },
                "required": ["title", "alert_ids", "severity", "what_happened", "related_signals",
                             "hypotheses", "next_check"],
                "additionalProperties": False,
            },
        },
        "noise_notes": {"type": "string",
                        "description": "Alertas que parecen ruido o derivadas de otra, y por qué. '' si no hay."},
    },
    "required": ["headline", "alerts", "noise_notes"],
    "additionalProperties": False,
}


def _metric_context() -> str:
    """Definiciones y lectura de las métricas monitoreadas, tomadas de la capa semántica."""
    sl = yaml.safe_load(SEMANTIC_LAYER_PATH.read_text(encoding="utf-8"))["metrics"]
    keys = {"payment_failure_rate", "logo_churn_rate", "churned_mrr", "trial_to_paid_conversion"}
    return yaml.safe_dump(
        {k: {f: sl[k][f] for f in ("name", "definition", "how_to_read") if f in sl[k]} for k in keys},
        allow_unicode=True, sort_keys=True)


def _series_context(panel: pd.DataFrame, alerts: pd.DataFrame, month: str) -> str:
    """Serie de los últimos meses de todas las métricas para cada segmento con alerta."""
    months = sorted(panel["month"].unique())
    end = months.index(month)
    window = months[max(0, end - WINDOW):end + 1]
    segs = alerts[["dimension", "segment"]].drop_duplicates()
    ctx = panel.merge(segs, on=["dimension", "segment"])
    ctx = ctx[ctx["month"].isin(window)]
    wide = ctx.pivot_table(index=["dimension", "segment", "metric"], columns="month",
                           values="rate", sort=True).round(4)
    return wide.to_csv()


def with_ids(alerts: pd.DataFrame) -> pd.DataFrame:
    """Agrega un id legible (A1, A2, …) a las alertas de un mes, en el orden del detector."""
    return alerts.reset_index(drop=True).assign(
        id=[f"A{i}" for i in range(1, len(alerts) + 1)])[["id", *alerts.columns]]


def summarize(panel: pd.DataFrame, alerts: pd.DataFrame, month: str,
              client: anthropic.Anthropic | None = None, period_desc: str | None = None) -> dict:
    """Pide a Claude el resumen del periodo. Devuelve el JSON de SUMMARY_SCHEMA.

    `month` es la clave del periodo en el panel (un mes o el inicio de una ventana de días);
    `period_desc` lo describe en palabras (por defecto, "mes YYYY-MM")."""
    client = client or anthropic.Anthropic()
    period_desc = period_desc or f"mes {month}"
    system = f"""Eres analista de negocio de un SaaS contable para pymes en LATAM. \
Cada periodo recibes las alertas de un detector estadístico de anomalías y escribes \
un resumen para el equipo de negocio.

<metricas>
{_metric_context()}
</metricas>

Cómo funciona el detector (umbral |z| >= {Z_THRESHOLD:g} y al menos {MIN_EXCESS} \
eventos de diferencia):
- Un periodo es un mes calendario o un rango de días; siempre se compara contra \
periodos anteriores de igual duración.
- "pico": la tasa del periodo contra los {WINDOW} periodos anteriores (la desviación \
tiene un piso binomial según el tamaño del segmento).
- "cambio de nivel": los últimos {RECENT} periodos juntos contra los {WINDOW} anteriores; \
señala un cambio gradual que empezó hace hasta {RECENT} periodos. En ese caso rate, num \
y den corresponden a los {RECENT} periodos agrupados.
"excess" = eventos por encima (o por debajo) de lo esperado según la base.
El detector solo usa cohortes de conversión cuyos trials ya terminaron, así que no hay \
datos incompletos en las alertas.

Cómo escribir:
- Agrupa las alertas que tienen una misma causa probable. Un problema en un país suele \
aparecer también en el total, en los planes y en los canales: trátalo como una sola \
alerta y menciona las derivadas en related_signals. En alert_ids pon los id de las \
alertas del detector que agrupa, empezando por la que mejor describe el problema. Cada \
id va en una sola alerta; las que sean ruido van solo en noise_notes. Los id son \
internos: no los escribas en los textos, nombra la métrica y el segmento.
- Ordena por impacto en el negocio, no por z-score.
- Las hipótesis son hipótesis: básalas en las series que recibes y en cómo leer cada \
métrica. No inventes eventos externos concretos (nombres de bancos, fechas de \
campañas); describe el tipo de causa y cómo verificarla.
- Si una alerta es débil (z cerca del umbral, base chica, un solo periodo) o una mejora \
sin relevancia, dilo en noise_notes en vez de darle una alerta propia.
- Usa solo cifras presentes en los datos o cuentas directas sobre ellas. Tasas en %.
- Escribe en español, claro y breve."""

    alerts_csv = with_ids(alerts)
    alerts_csv = (alerts_csv.assign(metric=alerts_csv["metric"].map(METRIC_LABELS))
                  .round(4).to_csv(index=False))
    n_series = int(((panel["month"] == month) & (panel["den"] >= MIN_BASE)).sum())
    user = f"""Periodo: {period_desc}
Series evaluadas en este periodo: {n_series} (métrica x segmento). Con tantas series, es \
esperable que alguna cruce el umbral por azar: una alerta aislada con z apenas sobre \
{Z_THRESHOLD:g} y pocos eventos de exceso, sin otras métricas que la acompañen, merece \
severidad baja o ir a noise_notes.

<alertas>
{alerts_csv}
</alertas>

<series_de_los_segmentos_con_alerta>
{_series_context(panel, alerts, month)}
</series_de_los_segmentos_con_alerta>

Escribe el resumen del periodo."""

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise AgentError("El modelo rechazó la solicitud.")
    if response.stop_reason == "max_tokens":
        raise AgentError("La respuesta del modelo quedó cortada (max_tokens).")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def _report_dir(key: str):
    # Meses (YYYY-MM) en reports/ (versionado); rangos de días en outputs/, que no se versiona
    return REPORTS_DIR if len(key) == 7 else OUTPUT_DIR / "alertas"


def report_path(key: str):
    return _report_dir(key) / f"alertas_{key}.json"


def save_report(summary: dict, alerts: pd.DataFrame, key: str, title: str | None = None):
    """Guarda el resumen en JSON (con las alertas que lo originaron) y en markdown.
    `key` es el mes (YYYY-MM) o el rango (YYYY-MM-DD_YYYY-MM-DD)."""
    folder = _report_dir(key)
    folder.mkdir(parents=True, exist_ok=True)
    report = {"month": key, "alerts": json.loads(with_ids(alerts).to_json(orient="records")),
              "summary": summary}
    report_path(key).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = folder / f"alertas_{key}.md"
    md_path.write_text(summary_to_markdown(summary, title or key), encoding="utf-8")
    return md_path


def load_report(month: str) -> dict | None:
    path = report_path(month)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def summary_to_markdown(summary: dict, month: str) -> str:
    lines = [f"# Alertas del periodo {month}", "", f"**{summary['headline']}**", ""]
    for a in summary["alerts"]:
        lines += [f"## [{a['severity'].upper()}] {a['title']}", "", a["what_happened"], ""]
        if a["related_signals"]:
            lines += [f"*Señales relacionadas:* {a['related_signals']}", ""]
        lines += ["**Hipótesis**", *[f"{i}. {h}" for i, h in enumerate(a["hypotheses"], 1)], "",
                  f"**Para confirmar:** {a['next_check']}", ""]
    if summary["noise_notes"]:
        lines += ["## Probable ruido", "", summary["noise_notes"], ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def _print_alerts(alerts: pd.DataFrame) -> None:
    view = alerts.assign(
        metric=alerts["metric"].map(METRIC_LABELS),
        rate=(alerts["rate"] * 100).round(1).astype(str) + "%",
        baseline=(alerts["baseline"] * 100).round(1).astype(str) + "%",
        z=alerts["z"].round(1), excess=alerts["excess"].round(0), den=alerts["den"].astype(int),
    )[["month", "metric", "dimension", "segment", "rule", "rate", "baseline", "z", "excess", "den", "direction"]]
    print(view.to_string(index=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Detector de anomalías en métricas del SaaS.")
    parser.add_argument("--month", help="Mes a revisar, formato YYYY-MM (por defecto el último).")
    parser.add_argument("--all", action="store_true", help="Lista las alertas de todos los meses (sin LLM).")
    parser.add_argument("--no-llm", action="store_true", help="Solo el detector, sin resumen de Claude.")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"No existe {DB_PATH}. Ejecuta primero: python generate_data.py", file=sys.stderr)
        return 1
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        panel = build_panel(con)
    months = sorted(panel["month"].unique())

    if args.all:
        alerts = detect(panel)
        _print_alerts(alerts)
        print(f"\n{len(alerts)} alertas en {alerts['month'].nunique()} meses "
              f"(evaluados: {months[WINDOW]} a {months[-1]}).")
        return 0

    month = args.month or months[-1]
    if month not in months[WINDOW:]:
        print(f"Mes inválido. Usa uno entre {months[WINDOW]} y {months[-1]}.", file=sys.stderr)
        return 1

    alerts = detect(panel, [month])
    if alerts.empty:
        print(f"Sin alertas en {month}.")
        return 0
    print(f"== {len(alerts)} alertas en {month}")
    _print_alerts(alerts)
    if args.no_llm:
        return 0

    try:
        summary = summarize(panel, alerts, month)
    except AgentError as e:
        print(f"\nNo se pudo generar el resumen: {e}", file=sys.stderr)
        return 1
    except anthropic.APIError as e:
        print(f"\nError de la API de Anthropic: {e}", file=sys.stderr)
        return 1

    path = save_report(summary, alerts, month)
    print("\n" + summary_to_markdown(summary, month))
    print(f"== Resumen guardado en {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
