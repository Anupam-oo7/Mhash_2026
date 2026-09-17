"""Co-bidding and entity network analysis using NetworkX."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

import config


def build_relationship_edges(relationships: pd.DataFrame, procurement: pd.DataFrame) -> pd.DataFrame:
    """Normalize relationships and add co-bid edges from multi-vendor notices."""
    rows: list[dict] = []
    if relationships is not None and not relationships.empty:
        for _, r in relationships.iterrows():
            rows.append(
                {
                    "entity_a": r.get("entity_a"),
                    "entity_b": r.get("entity_b"),
                    "relationship_type": r.get("relationship_type"),
                    "source": r.get("source"),
                    "confidence": r.get("confidence", 1.0),
                    "notice_id": r.get("notice_id"),
                    "weight": 1,
                }
            )

    # Co-bidding from vendors that appear on same notice with tendering parties
    if procurement is not None and not procurement.empty:
        if {"notice_id", "vendor_id"}.issubset(procurement.columns):
            for notice_id, g in procurement.dropna(subset=["vendor_id"]).groupby("notice_id"):
                vendors = sorted(set(g["vendor_id"].tolist()))
                for i in range(len(vendors)):
                    for j in range(i + 1, len(vendors)):
                        rows.append(
                            {
                                "entity_a": vendors[i],
                                "entity_b": vendors[j],
                                "relationship_type": "co_bid_with",
                                "source": "derived_same_notice",
                                "confidence": 0.5,
                                "notice_id": notice_id,
                                "weight": 1,
                            }
                        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "entity_a",
                "entity_b",
                "relationship_type",
                "source",
                "confidence",
                "notice_id",
                "weight",
            ]
        )

    edges = pd.DataFrame(rows)
    # Aggregate weights for co-bid / co-listed
    agg = (
        edges.groupby(
            ["entity_a", "entity_b", "relationship_type"], dropna=False
        )
        .agg(
            weight=("weight", "sum"),
            confidence=("confidence", "max"),
            source=("source", "first"),
            n_notices=("notice_id", "nunique"),
        )
        .reset_index()
    )
    return agg


def build_graph(
    edges: pd.DataFrame,
    organizations: pd.DataFrame | None = None,
    procurement: pd.DataFrame | None = None,
) -> nx.Graph:
    G = nx.Graph()

    org_names = {}
    if organizations is not None and not organizations.empty:
        for _, r in organizations.drop_duplicates("org_id").iterrows():
            org_names[r["org_id"]] = r.get("name")

    buyer_ids = set()
    vendor_ids = set()
    if procurement is not None and not procurement.empty:
        buyer_ids = set(procurement.get("buyer_id", pd.Series(dtype=str)).dropna())
        vendor_ids = set(procurement.get("vendor_id", pd.Series(dtype=str)).dropna())

    for _, e in edges.iterrows():
        a, b = e["entity_a"], e["entity_b"]
        if pd.isna(a) or pd.isna(b):
            continue
        for node in (a, b):
            if node not in G:
                ntype = "buyer" if node in buyer_ids else ("vendor" if node in vendor_ids else "organization")
                G.add_node(node, node_type=ntype, name=org_names.get(node, node))
        key = e["relationship_type"]
        if G.has_edge(a, b):
            G[a][b]["weight"] = G[a][b].get("weight", 0) + float(e.get("weight") or 1)
            types = set(G[a][b].get("types", []))
            types.add(key)
            G[a][b]["types"] = list(types)
        else:
            G.add_edge(
                a,
                b,
                weight=float(e.get("weight") or 1),
                types=[key],
                confidence=float(e.get("confidence") or 0),
            )
    return G


def graph_features(G: nx.Graph) -> pd.DataFrame:
    if G.number_of_nodes() == 0:
        return pd.DataFrame()

    degree = dict(G.degree())
    wdegree = dict(G.degree(weight="weight"))
    try:
        # Exact betweenness is too expensive for the full TED relationship graph.
        sample_size = min(500, G.number_of_nodes())
        betweenness = nx.betweenness_centrality(
            G,
            k=sample_size if G.number_of_nodes() > 2000 else None,
            weight="weight",
            seed=config.ML_RANDOM_STATE,
        )
    except Exception:  # noqa: BLE001
        betweenness = {n: 0.0 for n in G.nodes}

    components = {n: 0 for n in G.nodes}
    for i, comp in enumerate(nx.connected_components(G)):
        for n in comp:
            components[n] = i

    rows = []
    for n, data in G.nodes(data=True):
        rows.append(
            {
                "node_id": n,
                "name": data.get("name"),
                "node_type": data.get("node_type"),
                "degree": degree.get(n, 0),
                "weighted_degree": wdegree.get(n, 0),
                "betweenness": betweenness.get(n, 0),
                "component_id": components.get(n, 0),
            }
        )
    return pd.DataFrame(rows)


def repeated_cobid_pairs(edges: pd.DataFrame) -> pd.DataFrame:
    if edges is None or edges.empty:
        return pd.DataFrame()
    cobid = edges[edges["relationship_type"].isin(["co_bid_with", "co_listed_on_notice"])].copy()
    cobid = cobid[cobid["weight"] >= config.COBID_MIN_SHARED]
    return cobid.sort_values("weight", ascending=False)


def annotate_network_signals(procurement: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    df["repeated_cobid"] = False
    df["max_cobid_weight"] = 0
    if edges is None or edges.empty or df.empty:
        return df

    cobid = repeated_cobid_pairs(edges)
    if cobid.empty:
        return df

    partner_weight: dict[str, int] = defaultdict(int)
    for _, r in cobid.iterrows():
        w = int(r["weight"])
        partner_weight[r["entity_a"]] = max(partner_weight[r["entity_a"]], w)
        partner_weight[r["entity_b"]] = max(partner_weight[r["entity_b"]], w)

    df["max_cobid_weight"] = df["vendor_id"].map(partner_weight).fillna(0).astype(int)
    df["repeated_cobid"] = df["max_cobid_weight"] >= config.COBID_MIN_SHARED
    return df


def export_pyvis(G: nx.Graph, path: Path, max_nodes: int = 200) -> Path:
    """Write interactive HTML network. Falls back to GraphML if pyvis missing."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if G.number_of_nodes() > max_nodes:
        # Keep highest-degree subgraph for visualization
        top = sorted(G.degree, key=lambda x: x[1], reverse=True)[:max_nodes]
        G = G.subgraph([n for n, _ in top]).copy()

    try:
        from pyvis.network import Network

        net = Network(height="700px", width="100%", bgcolor="#ffffff", font_color="#222")
        color = {"buyer": "#1f77b4", "vendor": "#ff7f0e", "organization": "#7f7f7f", "tender": "#2ca02c"}
        for n, data in G.nodes(data=True):
            ntype = data.get("node_type", "organization")
            net.add_node(
                n,
                label=str(data.get("name") or n)[:40],
                title=f"{n}\n{data.get('name')}\ntype={ntype}",
                color=color.get(ntype, "#999"),
            )
        for a, b, data in G.edges(data=True):
            net.add_edge(a, b, value=data.get("weight", 1), title=str(data.get("types")))
        net.write_html(str(path))
    except Exception:
        nx.write_graphml(G, str(path.with_suffix(".graphml")))
        path = path.with_suffix(".graphml")
    return path
