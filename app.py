"""
App Streamlit (fase 5): pestañas "Pregunta" (agente text-to-SQL), "Alertas"
(detector de anomalías + resumen de Claude) y "Métricas" (capa semántica).

Uso:
    streamlit run app.py

El estilo visual vive en .streamlit/config.toml y assets/style.css.
"""

from __future__ import annotations

import copy
import html
import importlib
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import anthropic  # noqa: E402

import agent  # noqa: E402
import anomalies  # noqa: E402


def _reload_if_changed() -> None:
    """Streamlit vuelve a ejecutar app.py en cada interacción, pero puede dejar en memoria
    una versión vieja de src/*.py si se editaron con el servidor abierto. Si alguno cambió
    en disco desde que se cargó, se recargan los dos (anomalies importa de agent)."""
    global agent, anomalies
    mtimes = {m.__name__: Path(m.__file__).stat().st_mtime for m in (agent, anomalies)}
    if any(getattr(m, "_loaded_mtime", None) != mtimes[m.__name__] for m in (agent, anomalies)):
        agent = importlib.reload(agent)
        anomalies = importlib.reload(anomalies)
        for m in (agent, anomalies):
            m._loaded_mtime = mtimes[m.__name__]
        st.cache_data.clear()   # los datos cacheados pueden venir del código anterior


_reload_if_changed()

st.set_page_config(page_title="Analista de autoservicio", page_icon="📊", layout="wide")
st.markdown(f"<style>{(ROOT / 'assets' / 'style.css').read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True)

SEMANTIC = yaml.safe_load(agent.SEMANTIC_LAYER_PATH.read_text(encoding="utf-8"))
COLORWAY = ["#30ABA9", "#253E53", "#00E0C7", "#94A3B8", "#1A7E7F"]
MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
EXAMPLES = [
    "¿Cuál es el MRR actual?",
    "Compara la conversión trial a pago por canal en 2025 vs 2026",
    "¿Cómo evolucionó el churn voluntario de México en 2026?",
]
METRIC_SHORT = {
    "payment_failure_rate": "Fallo de pagos",
    "logo_churn_rate_voluntario": "Churn voluntario",
    "logo_churn_rate_involuntario": "Churn involuntario",
    "trial_to_paid_conversion": "Conversión trial→pago",
}
CHANNEL_NAMES = {"organico": "Orgánico", "paid_search": "Paid search",
                 "referido": "Referido", "contador_aliado": "Contador aliado"}
SEVERITY_DOT = {"alta": "bad", "media": "warn", "baja": "ok"}


# ---------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------
def fmt_num(x: float, decimals: int = 0) -> str:
    """Número con formato latino: 45.978,5"""
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_pct(x: float, decimals: int = 1) -> str:
    return f"{fmt_num(x * 100, decimals)}%"


def fmt_month(month: str) -> str:
    y, m = month.split("-")
    return f"{MONTHS_ES[int(m) - 1]} {y}"


def ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"hace {max(minutes, 1)} min"
    if minutes < 60 * 24:
        return f"hace {minutes // 60} h"
    return f"hace {minutes // (60 * 24)} días"


def esc(text: str) -> str:
    return html.escape(str(text))


def api_key_ok() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------
# Datos (cacheados)
# ---------------------------------------------------------------------
@st.cache_resource
def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(agent.DB_PATH), read_only=True)


def query(sql: str) -> pd.DataFrame:
    # Un cursor por consulta: la conexión cacheada se comparte entre sesiones
    return get_connection().cursor().execute(sql).df()


@st.cache_data(show_spinner=False)
def metric_series(key: str) -> pd.DataFrame:
    """Serie mensual de una métrica, con el SQL base de la capa semántica."""
    return query(SEMANTIC["metrics"][key]["sql"])


@st.cache_data(show_spinner=False)
def dataset_facts() -> dict:
    row = query("SELECT COUNT(*) AS pymes, COUNT(DISTINCT country_code) AS paises FROM companies").iloc[0]
    return {"pymes": int(row["pymes"]), "paises": int(row["paises"]),
            "corte": SEMANTIC["fecha_corte"]}


@st.cache_data(show_spinner=False)
def segment_names() -> dict:
    names = {"total": "Total", **CHANNEL_NAMES}
    names.update(dict(query("SELECT country_code, country_name FROM dim_countries").values))
    names.update(dict(query("SELECT plan_id, 'Plan ' || plan_name FROM dim_plans").values))
    return names


@st.cache_data(show_spinner=False)
def load_panel(period: tuple[date, date] | None = None) -> pd.DataFrame:
    """Panel mensual (sin period) o de ventanas de días iguales a `period`."""
    return anomalies.build_panel(get_connection().cursor(), period)


@st.cache_data(show_spinner=False)
def load_alerts() -> pd.DataFrame:
    return anomalies.detect(load_panel())


# ---------------------------------------------------------------------
# Gráficos (Plotly)
# ---------------------------------------------------------------------
def style_figure(fig: go.Figure, title: str, y_title: str | None, legend: bool) -> go.Figure:
    fig.update_layout(
        title=dict(text=esc(title), x=0, font=dict(size=15, color="#1B222D")),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color="#475569", size=13),
        margin=dict(l=8, r=8, t=56, b=8), height=360,
        showlegend=legend, barcornerradius=4, bargap=0.35,
        legend=dict(orientation="h", x=1, xanchor="right", y=1.02, yanchor="bottom", title=None),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#E2E8F0", font=dict(color="#0F172A")),
    )
    fig.update_xaxes(type="category", showgrid=False, linecolor="#E2E8F0", ticks="")
    fig.update_yaxes(showgrid=True, gridcolor="#E2E8F0", zeroline=False, title=y_title,
                     title_font=dict(size=12, color="#64748B"))
    return fig


def answer_chart(df: pd.DataFrame | None, chart: dict) -> go.Figure | None:
    """Gráfico sugerido por el agente. None si no aplica o no calza con el resultado."""
    if df is None or df.empty or chart.get("type") not in ("line", "bar"):
        return None
    data = agent.chart_data(df, chart, max_series=len(COLORWAY))
    if data is None or data.empty:
        return None
    x = [str(v) for v in data.index]
    fig = go.Figure()
    for i, col in enumerate(data.columns):
        if chart["type"] == "line":
            fig.add_trace(go.Scatter(x=x, y=data[col], name=str(col), line=dict(color=COLORWAY[i], width=2.5),
                                     mode="lines+markers" if len(x) <= 12 else "lines", marker=dict(size=7)))
        else:
            fig.add_trace(go.Bar(x=x, y=data[col], name=str(col), marker_color=COLORWAY[i]))
    single = data.shape[1] == 1
    return style_figure(fig, chart.get("title", ""), str(data.columns[0]) if single else None, not single)


def alert_chart(alert: dict, period: dict) -> go.Figure:
    """Serie reciente del segmento con alerta, con la tasa esperada como referencia."""
    panel = load_panel(period["range"])
    s = panel[(panel["metric"] == alert["metric"]) & (panel["dimension"] == alert["dimension"])
              & (panel["segment"] == alert["segment"]) & (panel["month"] <= alert["month"])].tail(12)
    x = [period["axis_label"](m) for m in s["month"]]
    fig = go.Figure(go.Scatter(x=x, y=s["rate"] * 100, name="Tasa", mode="lines+markers",
                               line=dict(color=COLORWAY[0], width=2.5), marker=dict(size=7)))
    fig.add_trace(go.Scatter(x=x, y=[alert["baseline"] * 100] * len(x), name="Esperado",
                             mode="lines", line=dict(color=COLORWAY[3], width=1.5, dash="dot")))
    return style_figure(fig, "", "%", True)


def plot(fig: go.Figure, key: str) -> None:
    # key única: una misma pregunta puede repetirse en el historial
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=key)


# ---------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------
TABS = ["Pregunta", "Alertas", "Métricas"]


def go_to_tab(tab: str) -> None:
    # st.tabs con key guarda la pestaña activa en session_state: se puede fijar desde el encabezado
    st.session_state["main_tabs"] = tab


def render_topbar() -> None:
    with st.container(key="topbar"):
        brand, _, *links = st.columns([3, 3, 1, 1, 1], vertical_alignment="center")
        brand.markdown('<div class="brand">Analista<span>IA</span></div>', unsafe_allow_html=True)
        for col, tab in zip(links, TABS):
            col.button(tab, key=f"nav_{tab}", on_click=go_to_tab, args=(tab,), type="tertiary")


def render_hero() -> None:
    facts = dataset_facts()
    corte = pd.Timestamp(facts["corte"])
    corte_txt = f"{corte.day} {MONTHS_ES[corte.month - 1]} {corte.year}"

    left, right = st.columns([1.3, 1], gap="large")
    with left:
        st.markdown(f"""
<span class="badge"><b>DEMO</b> Business Analytics · datos sintéticos</span>
<div class="eyebrow">Agente analítico de autoservicio</div>
<div class="hero-title">Respuestas de negocio <span class="accent">en segundos</span></div>
<p class="hero-sub">Pregunte en lenguaje natural sobre MRR, churn, conversión y más.
Definiciones consistentes, SQL verificable y alertas automáticas.</p>
<div class="hero-note">Datos al {corte_txt} · {fmt_num(facts['pymes'])} pymes · {facts['paises']} países</div>
""", unsafe_allow_html=True)

    mrr = metric_series("mrr")
    customers = metric_series("active_customers")
    churn = metric_series("logo_churn_rate")
    nrr = metric_series("net_revenue_retention")
    conv = metric_series("trial_to_paid_conversion")
    # La última cohorte de conversión está incompleta (trials abiertos): se usa la anterior
    conv_row = conv.iloc[-2]
    last, prev = mrr.iloc[-1], mrr.iloc[-2]
    delta = last["mrr_usd"] / prev["mrr_usd"] - 1
    updated = ago(time.time() - agent.DB_PATH.stat().st_mtime)

    with right:
        st.markdown(f"""
<div class="card notice">
  <div class="dot ok"></div>
  <div><div class="title">Datos actualizados</div>
  <div class="meta">Corte {corte_txt} · {updated}</div></div>
</div>
<div class="card card-kpi">
  <div class="label">MRR actual · {fmt_month(last['month'])}</div>
  <div class="value">US$ {fmt_num(last['mrr_usd'])}</div>
  <div class="{'delta-up' if delta >= 0 else 'delta-down'}">{'▲' if delta >= 0 else '▼'} {fmt_pct(abs(delta))} vs {fmt_month(prev['month'])}</div>
</div>
<div class="card">
  <div class="card-head"><span class="card-title">Indicadores del mes</span><span class="chip ok">Al día</span></div>
  <div class="row"><span>Clientes activos</span><b>{fmt_num(customers.iloc[-1]['active_customers'])}</b></div>
  <div class="row"><span>Churn de clientes ({fmt_month(churn.iloc[-1]['month'])})</span><b>{fmt_pct(churn.iloc[-1]['logo_churn_rate'])}</b></div>
  <div class="row"><span>NRR mensual</span><b>{fmt_num(nrr.iloc[-1]['nrr_monthly'] * 100, 1)}%</b></div>
  <div class="row"><span>Conversión trial→pago (cohorte {fmt_month(conv_row['signup_month'])})</span><b>{fmt_pct(conv_row['conversion_rate'])}</b></div>
</div>""", unsafe_allow_html=True)


# ---------------------------------------------------------------------
# Pestaña "Pregunta"
# ---------------------------------------------------------------------
def render_answer(result: agent.AgentResult, key: str) -> None:
    if not result.ok:
        st.markdown(f"<div class='card notice'><div class='dot warn'></div><div>"
                    f"<div class='title'>No pude responder esta pregunta</div>"
                    f"<div class='meta'>{esc(result.error)}</div></div></div>", unsafe_allow_html=True)
        return

    st.markdown(result.interpretation)
    for w in result.warnings:
        st.caption(f"⚠️ {w}")
    fig = answer_chart(result.df, result.chart)
    if fig is not None:
        plot(fig, key=f"answer_{key}")
    st.dataframe(result.df, width="stretch", hide_index=True)

    chips = f"<span class='chip ok'>métrica: {esc(result.metric_used)}</span>" if result.metric_used else ""
    if result.sql_errors:
        chips += " <span class='chip warn'>SQL corregido</span>"
    if chips:
        st.markdown(f"<div class='answer-meta'>{chips}</div>", unsafe_allow_html=True)
    with st.expander("Ver SQL"):
        st.code(result.sql, language="sql")
        for err in result.sql_errors:
            st.caption(f"Error corregido: {err.splitlines()[0]}")


def tab_question() -> None:
    if not api_key_ok():
        st.info("Para hacer preguntas, agrega tu ANTHROPIC_API_KEY en el archivo .env "
                "(ver .env.example) y reinicia la app. Las pestañas Alertas y Métricas "
                "funcionan sin ella.")
        return

    cols = st.columns(len(EXAMPLES))
    for col, example in zip(cols, EXAMPLES):
        if col.button(example, type="secondary", width="stretch"):
            st.session_state["pending"] = example

    messages = st.session_state.setdefault("messages", [])
    for i, msg in enumerate(messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                render_answer(msg["result"], key=str(i))

    typed = st.chat_input("Pregunta sobre MRR, churn, conversión, cobros…")
    question = typed or st.session_state.pop("pending", None)
    if not question:
        return

    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Analizando…"):
            try:
                result = agent.ask(question, con=get_connection().cursor())
            except anthropic.APIError as e:
                result = agent.AgentResult(question=question, error=f"Error de la API de Anthropic: {e}")
        render_answer(result, key=str(len(messages)))
    messages.append({"role": "assistant", "result": result})


# ---------------------------------------------------------------------
# Pestaña "Alertas"
# ---------------------------------------------------------------------
def fmt_day(d: date, year: bool = True) -> str:
    return f"{d.day} {MONTHS_ES[d.month - 1]}" + (f" {d.year}" if year else "")


def fmt_range(start: date, end: date) -> str:
    same_year = start.year == end.year
    return f"{fmt_day(start, year=not same_year)} – {fmt_day(end)}"


def make_period(kind: str, month: str | None = None, start: date | None = None,
                end: date | None = None) -> dict:
    """Describe el periodo a analizar: un mes calendario o un rango de días."""
    if kind == "mes":
        return {"key": month, "label": month, "range": None, "text": fmt_month(month),
                "unit": "meses", "axis_label": fmt_month,
                "desc": f"mes {month}"}
    days = (end - start).days + 1
    return {"key": f"{start}_{end}", "label": str(start), "range": (start, end),
            "text": fmt_range(start, end), "unit": "periodos",
            "axis_label": lambda s: f"{int(s[8:])} {MONTHS_ES[int(s[5:7]) - 1]} {s[2:4]}",
            "desc": (f"del {start} al {end} ({days} días), comparado con rangos anteriores "
                     f"de {days} días")}


def alert_title(a: dict, period: dict) -> str:
    names = segment_names()
    return f"{METRIC_SHORT[a['metric']]} · {names.get(a['segment'], a['segment'])} · {period['text']}"


def alert_meta(a: dict, period: dict) -> str:
    what = (f"últimos {anomalies.RECENT} {period['unit']}" if a["rule"] == "cambio de nivel"
            else "en el periodo")
    return (f"{fmt_pct(a['rate'])} {what} vs {fmt_pct(a['baseline'])} esperado · "
            f"z {fmt_num(a['z'], 1)} · {a['direction']}")


def segment_label(a: dict) -> str:
    """Segmento + métrica, sin el periodo: 'Colombia (fallo de pagos)'."""
    return f"{segment_names().get(a['segment'], a['segment'])} ({METRIC_SHORT[a['metric']].lower()})"


def notice_card(dot: str, title: str, meta: str, body: str = "") -> None:
    st.markdown(f"""
<div class="card notice">
  <div class="dot {dot}"></div>
  <div><div class="title">{esc(title)}</div><div class="meta">{esc(meta)}</div>{body}</div>
</div>""", unsafe_allow_html=True)


def summary_body(item: dict, related: list[dict]) -> str:
    hyps = "".join(f"<li>{esc(h)}</li>" for h in item["hypotheses"])
    rel = ""
    if related:
        rel = ("<div class='related'>También se ve en: "
               + esc(", ".join(segment_label(r) for r in related)) + "</div>")
    return f"""
<div class="alert-body">
  <div class="summary-title">{esc(item['title'])}</div>
  <p>{esc(item['what_happened'])}</p>
  {rel}
  <div class="label">Hipótesis de causa</div>
  <ol>{hyps}</ol>
  <div class="label">Para confirmar</div>
  <p>{esc(item['next_check'])}</p>
</div>"""


def readable_summary(summary: dict, records: dict) -> dict:
    """Reemplaza los id internos (A1, A2…) que el LLM a veces cita por el nombre del segmento."""
    def fix(text: str) -> str:
        def name(m: re.Match) -> str:
            a = records.get(m.group(0))
            return segment_label(a) if a else m.group(0)
        return re.sub(r"\bA\d+\b", name, text)

    out = copy.deepcopy(summary)
    out["headline"], out["noise_notes"] = fix(out["headline"]), fix(out["noise_notes"])
    for item in out["alerts"]:
        for k in ("title", "what_happened", "related_signals", "next_check"):
            item[k] = fix(item[k])
        item["hypotheses"] = [fix(h) for h in item["hypotheses"]]
    return out


def report_matches(report: dict | None, alerts: pd.DataFrame) -> bool:
    """El reporte guardado corresponde a las alertas actuales del detector."""
    if not report:
        return False
    saved = [(a["metric"], a["dimension"], a["segment"]) for a in report["alerts"]]
    return saved == list(alerts[["metric", "dimension", "segment"]].itertuples(index=False, name=None))


def period_selector() -> dict | None:
    """Controles de periodo. Devuelve el periodo elegido, o None si no es válido."""
    cutoff = date.fromisoformat(SEMANTIC["fecha_corte"])
    data_start = date(2024, 1, 1)
    kind = st.radio("Tipo de periodo", ["Rango de días", "Mes"], horizontal=True, key="alerts_kind")

    if kind == "Mes":
        alert_months = set(load_alerts()["month"])
        months = sorted(load_panel()["month"].unique())[anomalies.WINDOW:]
        month = st.selectbox("Mes", months[::-1], key="alerts_month",
                             format_func=lambda m: f"{fmt_month(m)} · con alertas" if m in alert_months else fmt_month(m))
        return make_period("mes", month=month)

    last30 = st.checkbox("Últimos 30 días", value=True, key="alerts_last30",
                         help=f"Hasta la fecha de corte de los datos ({fmt_day(cutoff)}).")
    default = (cutoff - timedelta(days=29), cutoff)
    picked = st.date_input("Rango de fechas", value=default, min_value=data_start, max_value=cutoff,
                           format="DD/MM/YYYY", disabled=last30, key="alerts_range")
    start, end = default if last30 else (tuple(picked) + (None,))[:2]
    if end is None:
        st.caption("Elige la fecha de inicio y la de fin en el calendario.")
        return None
    days = (end - start).days + 1
    if start - timedelta(days=days * anomalies.WINDOW) < data_start:
        st.warning(f"No hay historia suficiente para comparar {days} días con los "
                   f"{anomalies.WINDOW} periodos anteriores de igual duración. Elige un rango más corto "
                   "o más reciente.")
        return None
    return make_period("rango", start=start, end=end)


def tab_alerts() -> None:
    controls, _ = st.columns([1.4, 2])
    with controls:
        period = period_selector()
        run = st.button("Ejecutar detección", type="primary", disabled=period is None)
    if run and period is not None:
        st.session_state["alert_period"] = period
    st.caption(f"Cada periodo se compara con los {anomalies.WINDOW} anteriores de igual duración. "
               f"Reglas: pico (|z| ≥ {anomalies.Z_THRESHOLD:g}) y cambio de nivel (últimos "
               f"{anomalies.RECENT} periodos juntos), con al menos {anomalies.MIN_EXCESS} eventos de "
               "diferencia. En rangos cortos hay menos eventos, así que es más difícil que algo alerte.")

    period = st.session_state.get("alert_period")
    if not period:
        return
    panel = load_panel(period["range"])
    alerts = anomalies.with_ids(anomalies.detect(panel, [period["label"]]))
    if alerts.empty:
        notice_card("ok", "No existen alertas durante el periodo seleccionado.", f"Periodo: {period['text']}")
        return
    st.markdown(f"<div class='section-title'>{len(alerts)} alertas · {esc(period['text'])}</div>",
                unsafe_allow_html=True)

    month = period["key"]   # clave del periodo para los reportes y las keys de los gráficos
    records = {a["id"]: a for a in alerts.to_dict("records")}
    report = anomalies.load_report(month)
    if report is not None and report_matches(report, alerts):
        summary = readable_summary(report["summary"], records)
        grouped = set()
        for item in summary["alerts"]:
            ids = [i for i in item["alert_ids"] if i in records]
            if not ids:
                continue
            grouped.update(ids)
            main, related = records[ids[0]], [records[i] for i in ids[1:]]
            notice_card(SEVERITY_DOT[item["severity"]], alert_title(main, period), alert_meta(main, period),
                        summary_body(item, related))
            with st.expander("Ver serie"):
                plot(alert_chart(main, period), key=f"alert_{month}_{main['id']}")
        noise = [records[i] for i in records if i not in grouped]
        if noise or summary["noise_notes"]:
            body = f"<div class='alert-body'><p>{esc(summary['noise_notes'])}</p></div>" if summary["noise_notes"] else ""
            meta = ("Alertas sin tarjeta propia: " + " · ".join(segment_label(a) for a in noise)
                    if noise else "Notas del análisis sobre las alertas derivadas y las señales débiles")
            notice_card("ok", "Señales menores y probable ruido", meta, body)

        # Resumen completo al final: titular visible, detalle desplegable (repite las tarjetas)
        with st.container(key="period_summary"):
            st.markdown(f"<div class='card-title'>Resumen del periodo · {esc(period['text'])}</div>"
                        f"<p class='summary-headline'>{esc(summary['headline'])}</p>",
                        unsafe_allow_html=True)
            with st.expander("Ver resumen completo"):
                st.markdown(anomalies.summary_to_markdown(summary, period["text"]).split("\n", 1)[1])
    else:
        for a in records.values():
            dot = "ok" if a["direction"] == "mejora" else ("bad" if abs(a["z"]) >= 5 else "warn")
            notice_card(dot, alert_title(a, period), alert_meta(a, period))
            with st.expander("Ver serie"):
                plot(alert_chart(a, period), key=f"alert_{month}_{a['id']}")

    label = "Regenerar resumen con Claude" if report else "Generar resumen con Claude"
    if not api_key_ok():
        st.info("Agrega tu ANTHROPIC_API_KEY en .env para que Claude redacte el resumen con hipótesis de causa.")
    elif st.button(label, type="secondary"):
        with st.spinner("Claude está redactando el resumen…"):
            try:
                plain = alerts.drop(columns="id")
                summary = anomalies.summarize(panel, plain, period["label"], period_desc=period["desc"])
                anomalies.save_report(summary, plain, month, title=period["text"])
                st.rerun()
            except (anomalies.AgentError, anthropic.APIError) as e:
                st.error(f"No se pudo generar el resumen: {e}")

    with st.expander("Ver tabla del detector"):
        st.dataframe(alerts.assign(rate=(alerts["rate"] * 100).round(1), baseline=(alerts["baseline"] * 100).round(1),
                                   z=alerts["z"].round(1), excess=alerts["excess"].round(0)),
                     width="stretch", hide_index=True)


# ---------------------------------------------------------------------
# Pestaña "Métricas"
# ---------------------------------------------------------------------
def tab_metrics() -> None:
    rows = "".join(
        f"<tr><td>{esc(m['name'])}<br><code>{esc(key)}</code></td><td>{esc(m['definition'])}</td>"
        f"<td>{esc(m['owner'])}</td><td>{esc(m.get('how_to_read', '—'))}</td></tr>"
        for key, m in SEMANTIC["metrics"].items())
    rules = "".join(f"<li>{esc(r)}</li>" for r in SEMANTIC["agent_rules"])
    st.markdown(f"""
<p class="hero-sub">La capa semántica (<code>semantic_layer.yaml</code>) es la única fuente de verdad:
el agente parte de estas definiciones y de su SQL base, y no inventa métricas propias.</p>
<div class="card">
  <table class="metric-table">
    <thead><tr><th>Métrica</th><th>Definición</th><th>Dueño</th><th>Cómo leerla</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
<div class="card">
  <div class="card-title">Reglas que sigue el agente</div>
  <ul class="rules">{rules}</ul>
</div>""", unsafe_allow_html=True)


# ---------------------------------------------------------------------
def render_safely(render) -> None:
    """Un error en una pestaña se muestra ahí y no tumba las demás."""
    try:
        render()
    except Exception as e:  # st.rerun/st.stop heredan de BaseException, no se atrapan
        st.error(f"Ocurrió un error en esta sección: {e}")
        with st.expander("Detalle técnico"):
            st.exception(e)


def main() -> None:
    render_topbar()
    if not agent.DB_PATH.exists():
        st.error("No existe data/saas.duckdb. Ejecuta primero: python generate_data.py")
        return
    render_hero()
    tabs = st.tabs(TABS, key="main_tabs", on_change="rerun")
    for tab, render in zip(tabs, (tab_question, tab_alerts, tab_metrics)):
        with tab:
            render_safely(render)


main()
