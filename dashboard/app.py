"""Streamlit investigator dashboard for the procurement auditing system."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src import database as db
from src.explanations import explain_row

st.set_page_config(
    page_title="Procurement Investigation Assistant",
    layout="wide",
    initial_sidebar_state="expanded",
)

DISCLAIMER = (
    "Investigation-priority scores highlight unusual patterns for human review. "
    "They do **not** establish misconduct, fraud, or corruption."
)

st.markdown(
    """
    <style>
        .stApp {
            background: linear-gradient(180deg, #0b1220 0%, #111827 100%);
            color: #e5e7eb;
        }
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.7rem;
            font-weight: 700;
        }
        .metric-card {
            background: rgba(17, 24, 39, 0.85);
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 14px;
            padding: 1rem 1rem 0.8rem 1rem;
            box-shadow: 0 8px 20px rgba(0,0,0,0.12);
        }
        .risk-tag {
            display: inline-block;
            padding: 0.28rem 0.7rem;
            border-radius: 999px;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.03em;
            margin-right: 0.35rem;
            margin-bottom: 0.35rem;
        }
        .high { background: rgba(239, 68, 68, 0.18); color: #fca5a5; border: 1px solid rgba(239,68,68,0.45); }
        .very_high { background: rgba(220, 38, 38, 0.28); color: #fecaca; border: 1px solid rgba(248,113,113,0.75); }
        .moderate { background: rgba(245, 158, 11, 0.15); color: #fcd34d; border: 1px solid rgba(245,158,11,0.55); }
        .low { background: rgba(34, 197, 94, 0.14); color: #86efac; border: 1px solid rgba(34,197,94,0.45); }
        .signal-badge {
            display: inline-block;
            padding: 0.25rem 0.65rem;
            border-radius: 999px;
            font-size: 0.68rem;
            margin: 0.15rem 0.25rem 0.15rem 0;
            background: rgba(96, 165, 250, 0.14);
            color: #bfdbfe;
            border: 1px solid rgba(96,165,250,0.35);
        }
        .section-header {
            color: #f8fafc;
            font-size: 1.15rem;
            font-weight: 700;
            margin-top: 1rem;
            margin-bottom: 0.5rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def risk_badge(level: str) -> str:
    lvl = str(level).lower() if level else "low"
    return f'<span class="risk-tag {lvl}">{str(level).upper()}</span>'


@st.cache_data
def load_csv(name: str) -> pd.DataFrame:
    path = config.PROCESSED_DIR / f"{name}.csv"
    if not path.exists():
        path = config.OUTPUT_DIR / f"{name}.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.ParserError:
        try:
            return pd.read_csv(path, engine="python")
        except (pd.errors.ParserError, UnicodeDecodeError, OSError):
            st.warning(f"Could not load {name}.csv because the file is incomplete.")
            return pd.DataFrame()


def parse_signals(raw) -> list[dict]:
    if raw is None or pd.isna(raw):
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    return []


def score_card(label: str, value: str, delta: str = ""):
    st.markdown(f'<div class="metric-card"><div style="color:#94a3b8; font-size:0.75rem;">{label}</div><div style="font-size:1.8rem; font-weight:800; margin-top:0.2rem;">{value}</div><div style="color:#94a3b8; font-size:0.72rem; margin-top:0.15rem;">{delta}</div></div>', unsafe_allow_html=True)


def page_overview(proc: pd.DataFrame, cases: pd.DataFrame, vendors: pd.DataFrame, buyers: pd.DataFrame):
    st.title("Procurement Investigation Overview")
    st.info(DISCLAIMER)

    if proc.empty:
        st.warning("No procurement data loaded. Run the pipeline first.")
        return

    val = pd.to_numeric(proc.get("bid_amount"), errors="coerce").fillna(
        pd.to_numeric(proc.get("estimated_amount"), errors="coerce")
    )
    flagged = cases if not cases.empty else pd.DataFrame()
    avg_priority = float(flagged["priority_score"].mean()) if not flagged.empty else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        score_card("Procurements", f"{len(proc):,}", "total records")
    with c2:
        score_card("Vendors", f"{int(proc['vendor_id'].nunique()) if 'vendor_id' in proc else 0:,}", "active vendors")
    with c3:
        score_card("Buyers", f"{int(proc['buyer_id'].nunique()) if 'buyer_id' in proc else 0:,}", "entities")
    with c4:
        score_card("Value", f"€{val.sum():,.0f}", "estimated / award value")
    with c5:
        score_card("Flagged", f"{len(flagged):,}", f"avg priority {avg_priority:.1f}")

    if not flagged.empty:
        st.markdown('<div class="section-header">Suspicion distribution</div>', unsafe_allow_html=True)
        priority_cols = st.columns(4)
        with priority_cols[0]:
            score_card("High priority", f"{int((flagged['priority_score'].gt(75) & flagged['priority_score'].le(90)).sum()):,}", "> 75 to 90")
        with priority_cols[1]:
            score_card("Moderate", f"{int((flagged['priority_score'].gt(50) & flagged['priority_score'].le(75)).sum()):,}", "> 50 to 75")
        with priority_cols[2]:
            score_card("Low", f"{int((flagged['priority_score'] <= 50).sum()):,}", "<= 50")
        with priority_cols[3]:
            score_card("Avg score", f"{avg_priority:.1f}", "across cases")

    col1, col2 = st.columns(2)
    with col1:
        if not proc.empty and "publication_date" in proc.columns:
            tmp = proc.copy()
            tmp["publication_date"] = pd.to_datetime(tmp["publication_date"], errors="coerce")
            daily = tmp.dropna(subset=["publication_date"]).groupby(tmp["publication_date"].dt.date).size()
            st.plotly_chart(px.bar(daily, title="Procurement volume over time"), use_container_width=True)
    with col2:
        if not proc.empty and "num_submissions" in proc.columns:
            st.plotly_chart(px.histogram(proc, x="num_submissions", title="Bidder count distribution"), use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        if not flagged.empty and "priority_level" in flagged.columns:
            st.plotly_chart(px.pie(flagged, names="priority_level", title="Priority level mix"), use_container_width=True)
    with col4:
        if not proc.empty and "investigation_priority_score" in proc.columns:
            st.plotly_chart(px.histogram(proc, x="investigation_priority_score", title="Priority score distribution"), use_container_width=True)

    if not flagged.empty:
        st.markdown('<div class="section-header">Top flagged procurements</div>', unsafe_allow_html=True)
        top = flagged.sort_values("priority_score", ascending=False).head(10).copy()
        display_cols = [c for c in ("case_id", "priority_score", "priority_level", "vendor_name", "buyer_name", "signal_types", "contract_value") if c in top.columns]
        st.dataframe(top[display_cols], use_container_width=True, height=320)


def page_queue(cases: pd.DataFrame):
    st.title("Investigation Queue")
    st.info(DISCLAIMER)
    if cases.empty:
        st.warning("No cases yet. Run the pipeline first.")
        return

    df = cases.copy()
    f1, f2, f3, f4 = st.columns(4)
    levels = sorted(df["priority_level"].dropna().unique().tolist()) if "priority_level" in df.columns else []
    level = f1.multiselect("Priority level", levels)
    min_score = f2.slider("Min priority score", 0, 100, 51)
    signal = f3.text_input("Signal contains")
    buyer = f4.text_input("Buyer contains")

    view = df.copy()
    if "priority_score" in view.columns:
        view = view[view["priority_score"] >= min_score]
    if level:
        view = view[view["priority_level"].isin(level)]
    if signal:
        view = view[view.get("signal_types", pd.Series(dtype=str)).fillna("").str.contains(signal, case=False)]
    if buyer:
        view = view[view.get("buyer_name", pd.Series(dtype=str)).fillna("").str.contains(buyer, case=False)]

    view = view.sort_values("priority_score", ascending=False)

    def style_priority(x):
        color = (
            'rgba(220,38,38,0.25)' if x > 90 else
            'rgba(239,68,68,0.15)' if x > 75 else
            'rgba(245,158,11,0.12)' if x > 50 else
            'rgba(34,197,94,0.12)'
        )
        return f"background: {color}; color: white; font-weight: 700;"

    st.dataframe(
        view[[c for c in ("case_id", "priority_score", "priority_level", "data_confidence", "buyer_name", "vendor_name", "contract_value", "signal_types") if c in view.columns]].style.applymap(
            lambda v: style_priority(v) if isinstance(v, (int, float)) and pd.notna(v) else "",
            subset=["priority_score"],
        ),
        use_container_width=True,
        height=520,
    )

    if not view.empty:
        st.session_state["selected_case"] = st.selectbox("Open case", view["case_id"].tolist())


def page_case_detail(cases: pd.DataFrame, proc: pd.DataFrame, explanations: pd.DataFrame):
    st.title("Case Detail")
    if cases.empty:
        st.warning("No cases available.")
        return

    case_df = cases.copy()
    case_list = case_df["case_id"].tolist()
    selected = st.session_state.get("selected_case") or (case_list[0] if case_list else None)
    case_id = st.selectbox("Case", case_list, index=case_list.index(selected) if selected in case_list else 0)
    case = case_df[case_df["case_id"] == case_id].iloc[0]

    pscore = float(case.get("priority_score") or 0)
    st.markdown(
        f"<div class='metric-card'><div style='display:flex; justify-content:space-between; align-items:center;'><div><div style='color:#94a3b8; font-size:0.8rem;'>Case</div><div style='font-size:1.8rem; font-weight:800;'> {case_id} </div></div><div>{risk_badge(case.get('priority_level'))}</div></div><div style='margin-top:1rem; display:flex; gap:0.8rem; flex-wrap: wrap;'><div style='color:#cbd5e1;'>Priority: <strong>{pscore:.1f}/100</strong></div><div style='color:#cbd5e1;'>Confidence: <strong>{case.get('data_confidence')}</strong></div><div style='color:#cbd5e1;'>Signals: <strong>{case.get('n_signals') or 0}</strong></div></div></div>",
        unsafe_allow_html=True,
    )

    info_cols = st.columns(4)
    with info_cols[0]:
        st.write(f"**Buyer:** {case.get('buyer_name') or '—'}")
    with info_cols[1]:
        st.write(f"**Vendor:** {case.get('vendor_name') or '—'}")
    with info_cols[2]:
        st.write(f"**Value:** {case.get('currency') or 'EUR'} {case.get('contract_value') or '—'}")
    with info_cols[3]:
        st.write(f"**CPV:** {case.get('cpv_code') or '—'}")

    # Score composition
    st.markdown('<div class="section-header">Suspicion score breakdown</div>', unsafe_allow_html=True)
    score_cols = st.columns(5)
    comp_names = ["rule_score", "statistical_score", "behavior_score", "network_score", "ml_score"]
    for col, name in zip(score_cols, comp_names):
        val = float(case.get(name) or 0)
        with col:
            st.metric(name.replace('_score', '').replace('_', ' ').title(), f"{val:.1f}")
            st.progress(min(1.0, val / 40.0))

    # explanation and evidence
    st.markdown('<div class="section-header">Why this was flagged</div>', unsafe_allow_html=True)
    signals = parse_signals(case.get("signals_json"))
    if signals:
        for s in signals:
            sev = str(s.get("severity", "MEDIUM")).upper()
            bg = "#dc2626" if sev == "HIGH" else "#f59e0b" if sev == "MEDIUM" else "#22c55e"
            st.markdown(
                f"<div style='padding:0.9rem 1rem; border-left:4px solid {bg}; background:rgba(15,23,42,0.7); border-radius:0.7rem; margin-bottom:0.7rem;'>"
                f"<div style='font-weight:700; margin-bottom:0.25rem;'>{s.get('signal', 'SIGNAL')} <span style='color:{bg};'>[{sev}]</span></div>"
                f"<div style='color:#cbd5e1; margin-bottom:0.25rem;'>{s.get('evidence', '')}</div>"
                f"<div style='font-size:0.72rem; color:#94a3b8;'>Rule {s.get('rule_id')} · value {s.get('value')} · threshold {s.get('threshold')}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.info("No explicit rule signal in the current case data.")

    # peer context
    proc_match = proc[(proc.get("publication_id") == case.get("publication_id")) & (proc.get("lot_id") == case.get("lot_id"))]
    if not proc_match.empty:
        row = proc_match.iloc[0]
        st.markdown('<div class="section-header">Peer and evidence context</div>', unsafe_allow_html=True)
        peer_cols = st.columns(5)
        with peer_cols[0]:
            st.metric("Peer group", row.get("peer_group_id") or "—")
        with peer_cols[1]:
            st.metric("Peer size", row.get("peer_group_size") or 0)
        with peer_cols[2]:
            st.metric("Peer median", f"€{row.get('peer_median') or 0:,.0f}")
        with peer_cols[3]:
            st.metric("Price deviation", f"{row.get('pct_diff_peer_median') or 0:.1f}%")
        with peer_cols[4]:
            st.metric("ML anomaly", f"{row.get('ml_anomaly_score') or 0:.1f}")

    # explanation text
    expl = ""
    if not explanations.empty and case_id in explanations.get("case_id", pd.Series()).tolist():
        expl = explanations[explanations["case_id"] == case_id].iloc[0].get("explanation", "")
    else:
        expl = explain_row(case)
    st.code(expl, language="text")

    # investigator review
    st.markdown('<div class="section-header">Investigator review</div>', unsafe_allow_html=True)
    review_status = st.selectbox(
        "Status",
        ["NEW", "UNDER_REVIEW", "REVIEWED", "DISMISSED", "FOLLOW_UP"],
        index=["NEW", "UNDER_REVIEW", "REVIEWED", "DISMISSED", "FOLLOW_UP"].index(case.get("review_status") or "NEW"),
    )
    note = st.text_area("Investigator note", value=case.get("investigator_note") or "")
    if st.button("Save feedback"):
        conn = db.connect(); db.init_schema(conn); db.upsert_feedback(conn, case_id, review_status, note); conn.close(); st.success("Feedback saved.")


def page_vendor(vendors: pd.DataFrame, history: pd.DataFrame):
    st.title("Vendor Profile")
    if vendors.empty:
        st.warning("No vendor summary available.")
        return
    vid = st.selectbox("Vendor", vendors["vendor_id"].tolist())
    row = vendors[vendors["vendor_id"] == vid].iloc[0]
    st.markdown(f"<div class='metric-card'><div style='font-size:1.5rem; font-weight:700;'>{row.get('vendor_name') or vid}</div><div style='color:#94a3b8;'>Vendor ID: {vid}</div></div>", unsafe_allow_html=True)
    vendor_cols = st.columns(4)
    with vendor_cols[0]:
        st.metric("Participation", int(row.get("total_tenders") or 0))
    with vendor_cols[1]:
        st.metric("Wins", int(row.get("total_wins") or 0))
    with vendor_cols[2]:
        st.metric("Win rate", f"{(row.get('win_rate') or 0) * 100:.1f}%")
    with vendor_cols[3]:
        st.metric("Value", f"€{row.get('total_contract_value') or 0:,.0f}")

    if not history.empty:
        h = history[history["vendor_id"] == vid].copy()
        if not h.empty and "publication_date" in h.columns:
            h["publication_date"] = pd.to_datetime(h["publication_date"], errors="coerce")
            st.plotly_chart(px.scatter(h, x="publication_date", y=pd.to_numeric(h.get("bid_amount"), errors="coerce").fillna(pd.to_numeric(h.get("estimated_amount"), errors="coerce")), title="Vendor timeline"), use_container_width=True)


def page_buyer(buyers: pd.DataFrame):
    st.title("Buyer Profile")
    if buyers.empty:
        st.warning("No buyer summary available.")
        return
    bid = st.selectbox("Buyer", buyers["buyer_id"].tolist())
    row = buyers[buyers["buyer_id"] == bid].iloc[0]
    st.markdown(f"<div class='metric-card'><div style='font-size:1.5rem; font-weight:700;'>{row.get('buyer_name') or bid}</div><div style='color:#94a3b8;'>Buyer ID: {bid}</div></div>", unsafe_allow_html=True)
    buyer_cols = st.columns(4)
    with buyer_cols[0]:
        st.metric("Total procurements", int(row.get("total_procurements") or 0))
    with buyer_cols[1]:
        st.metric("Total value", f"€{row.get('total_value') or 0:,.0f}")
    with buyer_cols[2]:
        st.metric("Vendor concentration", f"{(row.get('hhi') or 0):.3f}")
    with buyer_cols[3]:
        st.metric("Top vendor share", f"{(row.get('top_vendor_share') or 0) * 100:.1f}%")


def page_network():
    st.title("Network Graph")
    st.info("Repeated co-participation means vendors appeared together — not that they colluded.")
    html = config.OUTPUT_DIR / "network.html"
    if html.exists():
        st.components.v1.html(html.read_text(encoding="utf-8"), height=780, scrolling=True)
    else:
        st.warning("Network file not found. Run the pipeline first.")


def page_data_quality():
    st.title("Data Quality")
    report = config.OUTPUT_DIR / "data_quality.md"
    if report.exists():
        st.markdown(report.read_text(encoding="utf-8"))
    else:
        st.warning("No quality report yet.")


def main():
    st.sidebar.title("Procurement Audit")
    page = st.sidebar.radio(
        "Pages",
        [
            "Overview",
            "Investigation Queue",
            "Case Detail",
            "Vendor Profile",
            "Buyer Profile",
            "Network",
            "Data Quality",
        ],
    )

    proc = load_csv("procurement_data")
    cases = load_csv("cases")
    vendors = load_csv("vendor_summary")
    buyers = load_csv("buyer_summary")
    history = load_csv("vendor_history")
    explanations = load_csv("explanations")

    if page == "Overview":
        page_overview(proc, cases, vendors, buyers)
    elif page == "Investigation Queue":
        page_queue(cases)
    elif page == "Case Detail":
        page_case_detail(cases, proc, explanations)
    elif page == "Vendor Profile":
        page_vendor(vendors, history)
    elif page == "Buyer Profile":
        page_buyer(buyers)
    elif page == "Network":
        page_network()
    elif page == "Data Quality":
        page_data_quality()


if __name__ == "__main__":
    main()
