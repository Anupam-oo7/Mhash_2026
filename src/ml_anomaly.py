"""Unsupervised ML anomaly detection (Isolation Forest).

Produces a behavioral anomaly score — NOT a corruption probability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

import config


FEATURE_COLS = [
    "price_ratio",
    "num_submissions",
    "pct_diff_peer_median",
    "vendor_win_rate",
    "buyer_vendor_count",
    "bid_amount",
    "estimated_amount",
    "max_cobid_weight",
    "peer_percentile",
    "robust_z",
]


def add_ml_anomaly_scores(procurement: pd.DataFrame) -> pd.DataFrame:
    df = procurement.copy()
    df["ml_anomaly_score"] = np.nan
    df["ml_is_outlier"] = False

    if not config.ML_ENABLED or df.empty:
        return df

    features = []
    for c in FEATURE_COLS:
        if c in df.columns:
            features.append(c)
    if len(features) < 3:
        return df

    X = df[features].apply(pd.to_numeric, errors="coerce")
    # Log-transform heavily skewed amount columns
    for c in ("bid_amount", "estimated_amount"):
        if c in X.columns:
            X[c] = np.log1p(X[c].clip(lower=0))

    mask = X.notna().all(axis=1)
    if mask.sum() < config.ML_MIN_SAMPLES:
        return df

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X.loc[mask])
    model = IsolationForest(
        contamination=config.ML_CONTAMINATION,
        random_state=config.ML_RANDOM_STATE,
        n_estimators=200,
    )
    model.fit(Xs)
    # decision_function: higher = more normal; invert & scale to 0-100 anomaly
    raw = -model.decision_function(Xs)
    # Min-max to 0-100
    rmin, rmax = raw.min(), raw.max()
    scaled = (raw - rmin) / (rmax - rmin) * 100 if rmax > rmin else np.zeros_like(raw)
    preds = model.predict(Xs)

    df.loc[mask, "ml_anomaly_score"] = scaled
    df.loc[mask, "ml_is_outlier"] = preds == -1
    df.attrs["ml_features"] = features
    return df
