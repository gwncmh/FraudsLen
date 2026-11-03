"""Mốc so sánh: luật viết tay (M0), Logistic Regression (M1), Decision Tree (M2).
Dùng chung cho train (val_B) và final_test (test)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier

from fraudlens.common.features import CATEGORICAL, FEATURES

# tên mốc -> có phải xác suất không (điểm luật thì không, bỏ số đo hiệu chỉnh)
BASELINES = {"rules": False, "lr": True, "tree": True}


def rules_score(F: pd.DataFrame) -> np.ndarray:
    """Luật viết tay kiểu truyền thống: cộng điểm theo từng dấu hiệu."""
    return ((F["amt_ratio_30d"] > 3).astype(int) + F["is_night"] + F["is_new_category_30d"]
            + (F["n_txn_1h"] >= 3).astype(int) + (F["amt"] > 500).astype(int)).to_numpy(float)


def _one_hot(F: pd.DataFrame, columns: pd.Index | None = None) -> pd.DataFrame:
    X = pd.get_dummies(F[FEATURES], columns=CATEGORICAL, dtype=float)
    return X if columns is None else X.reindex(columns=columns, fill_value=0.0)


def lr_baseline(Ftr: pd.DataFrame, ytr: np.ndarray, *Fevs: pd.DataFrame) -> list[np.ndarray]:
    """Huấn luyện LR một lần trên Ftr, trả về điểm cho từng tập trong Fevs."""
    num = [c for c in FEATURES if c not in CATEGORICAL]
    Xtr = _one_hot(Ftr)
    mu, sd = Xtr[num].mean(), Xtr[num].std().replace(0, 1)
    Xtr[num] = (Xtr[num] - mu) / sd
    lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Xtr.to_numpy(), ytr)
    out = []
    for Fev in Fevs:
        Xev = _one_hot(Fev, Xtr.columns)
        Xev[num] = (Xev[num] - mu) / sd
        out.append(lr.predict_proba(Xev.to_numpy())[:, 1])
    return out


def tree_baseline(Ftr: pd.DataFrame, ytr: np.ndarray, *Fevs: pd.DataFrame,
                  max_depth: int = 8, min_samples_leaf: int = 200) -> list[np.ndarray]:
    """Một cây quyết định (giới hạn độ sâu và cỡ lá để không học thuộc), trả về điểm cho từng tập trong Fevs."""
    Xtr = _one_hot(Ftr)
    dt = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=min_samples_leaf, class_weight="balanced",
                                random_state=42).fit(Xtr.to_numpy(), ytr)
    return [dt.predict_proba(_one_hot(Fev, Xtr.columns).to_numpy())[:, 1] for Fev in Fevs]


def baseline_scores(Ftr: pd.DataFrame, ytr: np.ndarray, Fsel: pd.DataFrame, Fev: pd.DataFrame
                    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Điểm của mọi mốc: {tên: (điểm trên tập chọn ngưỡng, điểm trên tập đánh giá)}."""
    lr_sel, lr_ev = lr_baseline(Ftr, ytr, Fsel, Fev)
    dt_sel, dt_ev = tree_baseline(Ftr, ytr, Fsel, Fev)
    return {"rules": (rules_score(Fsel), rules_score(Fev)), "lr": (lr_sel, lr_ev), "tree": (dt_sel, dt_ev)}
