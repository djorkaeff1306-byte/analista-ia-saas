"""
Agente text-to-SQL (fase 2).

Flujo:
    pregunta -> SQL (Claude, a partir de semantic_layer.yaml) -> ejecución en DuckDB
    -> si falla, Claude corrige el SQL (máx. 2 reintentos)
    -> tabla + gráfico + interpretación de 2-3 frases.

Uso:
    python src/agent.py "¿Cómo evolucionó el MRR de México en 2026?"

Requiere ANTHROPIC_API_KEY en .env (ver .env.example).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import duckdb
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from matplotlib.figure import Figure

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "saas.duckdb"
SEMANTIC_LAYER_PATH = ROOT / "semantic_layer.yaml"
OUTPUT_DIR = ROOT / "outputs"

load_dotenv(ROOT / ".env")

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")
MAX_RETRIES = 2            # reintentos de corrección después del primer intento
MAX_ROWS_TO_LLM = 100      # filas del resultado que ve el modelo al interpretar
MAX_SERIES = 8             # series máximas en un gráfico; el resto se agrupa en "Otros"
# Si el modelo rechaza la solicitud, la API la reintenta en otro modelo (server-side)
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Paleta categórica validada (orden fijo, nunca cíclico)
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK_PRIMARY, INK_SECONDARY, INK_MUTED = "#0b0b0b", "#52514e", "#898781"
SURFACE, GRID = "#fcfcfb", "#e6e5e0"

# ---------------------------------------------------------------------
# Esquemas de salida estructurada
# ---------------------------------------------------------------------
SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "metric_used": {
            "type": "string",
            "description": "Clave de la métrica de la capa semántica en la que se basa el SQL, o '' si ninguna.",
        },
        "sql": {
            "type": "string",
            "description": "Una sola consulta SELECT/WITH para DuckDB. '' si la pregunta no se puede responder.",
        },
        "notes": {
            "type": "string",
            "description": "Supuestos tomados o, si sql está vacío, por qué no se puede responder.",
        },
        "chart": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["line", "bar", "none"]},
                "title": {"type": "string"},
                "x": {"type": "string", "description": "Columna del eje X."},
                "y": {"type": "array", "items": {"type": "string"},
                      "description": "Columnas numéricas a graficar (misma escala)."},
                "color": {"type": "string",
                          "description": "Columna categórica que separa series, o ''."},
            },
            "required": ["type", "title", "x", "y", "color"],
            "additionalProperties": False,
        },
    },
    "required": ["metric_used", "sql", "notes", "chart"],
    "additionalProperties": False,
}

INTERPRETATION_SCHEMA = {
    "type": "object",
    "properties": {
        "interpretation": {
            "type": "string",
            "description": "2-3 frases con cifras concretas que respondan la pregunta.",
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Advertencias exigidas por agent_rules (datos incompletos, bases chicas, etc.).",
        },
    },
    "required": ["interpretation", "warnings"],
    "additionalProperties": False,
}


class AgentError(Exception):
    """Error del agente que se muestra tal cual al usuario."""


@dataclass
class AgentResult:
    question: str
    sql: str = ""
    metric_used: str = ""
    notes: str = ""
    chart: dict = field(default_factory=dict)
    df: pd.DataFrame | None = None
    interpretation: str = ""
    warnings: list[str] = field(default_factory=list)
    attempts: int = 0                              # ejecuciones de SQL realizadas
    sql_errors: list[str] = field(default_factory=list)
    error: str = ""                                # vacío = respuesta exitosa

    @property
    def ok(self) -> bool:
        return not self.error and self.df is not None


# ---------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------
def build_system_prompt(semantic_layer_text: str) -> str:
    return f"""Eres un analista de datos de un SaaS contable para pymes en LATAM. \
Respondes preguntas de negocio escribiendo SQL para DuckDB sobre la base descrita \
en la capa semántica.

<capa_semantica>
{semantic_layer_text}
</capa_semantica>

Cómo trabajar:
- La capa semántica es la única fuente de verdad para tablas, columnas y definiciones \
de métricas. Si la pregunta coincide con una métrica o un sinónimo, parte de su SQL \
base y agrega filtros o GROUP BY; no inventes definiciones alternativas.
- Sigue todas las agent_rules.
- Escribe una sola consulta SELECT (puede empezar con WITH). Nada de DDL, DML ni \
varias sentencias.
- Usa nombres de columna claros y ordena las series temporales por mes.
- Si la respuesta va a necesitar cifras derivadas (participación sobre el total, \
promedio por cliente, variación entre periodos, diferencia contra el promedio), \
calcúlalas como columnas en el SQL. La interpretación solo podrá citar cifras que \
estén en el resultado.
- Si la pregunta no se puede responder con las tablas disponibles, deja sql vacío y \
explica por qué en notes.
- Propón el gráfico que mejor responda la pregunta: line para series temporales, bar \
para comparar categorías, none si el resultado es un solo número o el gráfico no \
aporta. En y pon solo columnas de la misma escala (no mezcles montos con tasas). Si \
hay una columna categórica que separa series (p. ej. país), ponla en color.
- Escribe todo en español."""


def _result_message(df: pd.DataFrame) -> str:
    n = len(df)
    shown = df.head(MAX_ROWS_TO_LLM)
    header = (f"La consulta devolvió {n} filas."
              if n <= MAX_ROWS_TO_LLM
              else f"La consulta devolvió {n} filas; abajo están solo las primeras {MAX_ROWS_TO_LLM}.")
    return f"""{header}

<resultado>
{shown.to_csv(index=False)}
</resultado>

Interpreta el resultado para quien hizo la pregunta: 2-3 frases con cifras concretas, \
sin repetir la tabla.

Reglas para las cifras y afirmaciones:
- Cita solo cifras que aparezcan en el resultado (puedes redondearlas o pasarlas a %). \
No calcules cifras nuevas de cabeza (participaciones, promedios, cocientes, \
variaciones): si una no está en la tabla, no la menciones.
- Cada comparación ("mayor que", "el doble", "el más alto") debe poder leerse \
directamente de columnas del resultado.
- No afirmes causas ni cosas que la tabla no muestra (tendencias de otros periodos, \
composición por plan o precio si no están en el resultado, proyecciones). Si una \
lectura de la capa semántica ayuda, preséntala como referencia general, no como hecho.

En warnings incluye lo que exijan las agent_rules (último mes o cohorte incompleta, \
tasas sobre menos de 50 clientes, churn sin separar) y cualquier supuesto importante, \
con las mismas reglas: si algo no se puede comprobar con estas filas (p. ej. el tamaño \
de una base que no se consultó), no lo afirmes. Deja warnings vacío si no aplica ninguno."""


# ---------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------
def _call_claude(client: anthropic.Anthropic, system: str, messages: list, schema: dict) -> dict:
    """Llama a Claude con salida JSON estructurada y agrega su respuesta al historial."""
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        # La capa semántica es un prefijo estable: se cachea entre llamadas
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise AgentError("El modelo rechazó la solicitud.")
    if response.stop_reason == "max_tokens":
        raise AgentError("La respuesta del modelo quedó cortada (max_tokens).")

    # Se agrega el contenido completo (no solo el texto) para conservar el historial intacto
    messages.append({"role": "assistant", "content": response.content})
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# ---------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------
def run_sql(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    """Ejecuta una única consulta de lectura. La conexión además es read-only."""
    cleaned = sql.strip().rstrip(";").strip()
    if not re.match(r"(?is)^(select|with)\b", cleaned):
        raise ValueError("Solo se permiten consultas SELECT o WITH.")
    if ";" in cleaned:
        raise ValueError("Solo se permite una sentencia SQL.")
    return con.execute(cleaned).df()


# ---------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------
def ask(question: str,
        client: anthropic.Anthropic | None = None,
        con: duckdb.DuckDBPyConnection | None = None) -> AgentResult:
    """Responde una pregunta de negocio. Nunca lanza AgentError: lo deja en result.error."""
    client = client or anthropic.Anthropic()
    own_con = con is None
    if own_con:
        if not DB_PATH.exists():
            raise FileNotFoundError(f"No existe {DB_PATH}. Ejecuta primero: python generate_data.py")
        con = duckdb.connect(str(DB_PATH), read_only=True)

    system = build_system_prompt(SEMANTIC_LAYER_PATH.read_text(encoding="utf-8"))
    messages: list = [{"role": "user", "content": f"Pregunta: {question}"}]
    result = AgentResult(question=question)

    try:
        plan = _call_claude(client, system, messages, SQL_SCHEMA)
        for attempt in range(MAX_RETRIES + 1):
            result.sql = plan["sql"].strip()
            result.metric_used = plan["metric_used"]
            result.notes = plan["notes"]
            result.chart = plan["chart"]
            if not result.sql:
                result.error = plan["notes"] or "La pregunta no se puede responder con los datos disponibles."
                return result

            result.attempts = attempt + 1
            try:
                result.df = run_sql(con, result.sql)
                break
            except (duckdb.Error, ValueError) as e:
                result.sql_errors.append(str(e))
                if attempt == MAX_RETRIES:
                    result.error = f"El SQL siguió fallando después de {MAX_RETRIES} correcciones: {e}"
                    return result
                messages.append({"role": "user", "content":
                                 f"La consulta falló en DuckDB con este error:\n{e}\n\n"
                                 "Corrige el SQL y devuelve la respuesta completa otra vez."})
                plan = _call_claude(client, system, messages, SQL_SCHEMA)

        messages.append({"role": "user", "content": _result_message(result.df)})
        interp = _call_claude(client, system, messages, INTERPRETATION_SCHEMA)
        result.interpretation = interp["interpretation"]
        result.warnings = interp["warnings"]
    except AgentError as e:
        result.error = str(e)
    finally:
        if own_con:
            con.close()
    return result


# ---------------------------------------------------------------------
# Gráfico
# ---------------------------------------------------------------------
def chart_data(df: pd.DataFrame, chart: dict, max_series: int = MAX_SERIES) -> pd.DataFrame | None:
    """Devuelve una tabla ancha: índice = eje X, una columna por serie."""
    x, ys, color = chart.get("x", ""), chart.get("y", []), chart.get("color", "")
    needed = [x, *ys] + ([color] if color else [])
    if not ys or any(c not in df.columns for c in needed):
        return None
    if color:
        data = df.pivot_table(index=x, columns=color, values=ys[0], aggfunc="sum", sort=True)
        data.columns = [str(c) for c in data.columns]
    else:
        data = df.set_index(x)[ys]
    data = data.apply(pd.to_numeric, errors="coerce")
    if data.shape[1] > max_series:
        # Nunca se generan colores nuevos: las series menores se agrupan en "Otros"
        order = data.sum().sort_values(ascending=False).index
        keep = list(order[:max_series - 1])
        data = data[keep].assign(Otros=data.drop(columns=keep).sum(axis=1))
    return data


def build_chart(df: pd.DataFrame | None, chart: dict) -> Figure | None:
    """Arma el gráfico sugerido por el agente. None si no aplica o la sugerencia no calza con df."""
    if df is None or df.empty or chart.get("type") not in ("line", "bar"):
        return None
    data = chart_data(df, chart)
    if data is None or data.empty:
        return None

    fig = Figure(figsize=(9, 4.5), facecolor=SURFACE)
    ax = fig.subplots()
    ax.set_facecolor(SURFACE)
    labels = [str(v) for v in data.index]
    pos = np.arange(len(labels))

    if chart["type"] == "line":
        show_markers = len(labels) <= 12
        for i, col in enumerate(data.columns):
            ax.plot(pos, data[col], color=SERIES_COLORS[i], linewidth=2,
                    marker="o" if show_markers else None, markersize=6, label=col)
    else:
        n = data.shape[1]
        width = 0.8 / n
        for i, col in enumerate(data.columns):
            ax.bar(pos - 0.4 + width * (i + 0.5), data[col], width=width * 0.92,
                   color=SERIES_COLORS[i], label=col, edgecolor=SURFACE, linewidth=1)

    # Etiquetas del eje X: como máximo ~12 visibles
    step = max(1, len(labels) // 12)
    ax.set_xticks(pos[::step])
    ax.set_xticklabels(labels[::step], rotation=45 if len(labels) > 6 else 0,
                       ha="right" if len(labels) > 6 else "center")

    ax.set_title(chart.get("title", ""), color=INK_PRIMARY, loc="left", fontsize=12)
    ax.set_xlabel(chart["x"], color=INK_SECONDARY)
    if data.shape[1] == 1:
        ax.set_ylabel(data.columns[0], color=INK_SECONDARY)
    else:
        ax.legend(frameon=False, labelcolor=INK_SECONDARY)
    ax.tick_params(colors=INK_MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Pregúntale a los datos del SaaS en lenguaje natural.")
    parser.add_argument("question", help="Pregunta de negocio, entre comillas.")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY. Cópiala en el archivo .env (ver .env.example).", file=sys.stderr)
        return 1

    try:
        result = ask(args.question)
    except anthropic.AuthenticationError:
        print("ANTHROPIC_API_KEY es inválida. Revisa el archivo .env.", file=sys.stderr)
        return 1
    except anthropic.RateLimitError:
        print("Se alcanzó el límite de uso de la API; intenta en un momento.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"Error de la API de Anthropic ({e.status_code}): {e.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("No se pudo conectar con la API de Anthropic.", file=sys.stderr)
        return 1

    if result.sql:
        print(f"== SQL (intentos: {result.attempts}, métrica base: {result.metric_used or 'ninguna'})")
        print(result.sql)
    for i, err in enumerate(result.sql_errors, 1):
        print(f"\n-- Error {i} corregido: {err.splitlines()[0]}")
    if result.error:
        print(f"\n== No se pudo responder\n{result.error}")
        return 1

    print(f"\n== Resultado ({len(result.df)} filas)")
    print(result.df.head(30).to_string(index=False))
    print(f"\n== Interpretación\n{result.interpretation}")
    if result.warnings:
        print("\n== Advertencias")
        for w in result.warnings:
            print(f"- {w}")

    fig = build_chart(result.df, result.chart)
    if fig is not None:
        OUTPUT_DIR.mkdir(exist_ok=True)
        path = OUTPUT_DIR / "ultimo_grafico.png"
        fig.savefig(path, dpi=120)
        print(f"\n== Gráfico guardado en {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
