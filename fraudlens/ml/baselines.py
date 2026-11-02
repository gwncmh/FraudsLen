"""Mốc so sánh: luật viết tay và Logistic Regression. Dùng chung cho train (val_B) và final_test (test)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from fraudlens.common.features import CATEGORICAL, FEATURES


def rules_score(F: pd.DataFrame) -> np.ndarray:
    """Luật viết tay kiểu truyền thống: cộng điểm theo từng dấu hiệu."""
    return ((F["amt_ratio_30d"] > 3).astype(int) + F["is_night"] + F["is_new_category_30d"]
            + (F["n_txn_1h"] >= 3).astype(int) + (F["amt"] > 500).astype(int)).to_numpy(float)


def lr_baseline(Ftr: pd.DataFrame, ytr: np.ndarray, *Fevs: pd.DataFrame) -> list[np.ndarray]:
    """Huấn luyện LR một lần trên Ftr, trả về điểm cho từng tập trong Fevs."""
    num = [c for c in FEATURES if c not in CATEGORICAL]
    Xtr = pd.get_dummies(Ftr[FEATURES], columns=CATEGORICAL, dtype=float)
    mu, sd = Xtr[num].mean(), Xtr[num].std().replace(0, 1)
    Xtr[num] = (Xtr[num] - mu) / sd
    lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Xtr.to_numpy(), ytr)
    out = []
    for Fev in Fevs:
        Xev = pd.get_dummies(Fev[FEATURES], columns=CATEGORICAL, dtype=float).reindex(columns=Xtr.columns, fill_value=0.0)
        Xev[num] = (Xev[num] - mu) / sd
        out.append(lr.predict_proba(Xev.to_numpy())[:, 1])
    return out
