"""Bọc model XGBoost + hiệu chỉnh Platt + giải thích theo từng giao dịch. Dùng chung cho offline và Scorer."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from fraudlens.common.features import CATEGORICAL, FEATURE_TYPES, FEATURES, category_name, to_model_frame

EPS = 1e-6


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def platt_apply(p_raw: np.ndarray, a: float, b: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(a * logit(p_raw) + b)))


def dmatrix(X: np.ndarray, nthread: int = 1) -> xgb.DMatrix:
    """DMatrix với tên và kiểu cột khai báo tường minh (cột category là cột phân loại)."""
    return xgb.DMatrix(X, feature_names=FEATURES, feature_types=FEATURE_TYPES, enable_categorical=True,
                       nthread=nthread)


class FraudModel:
    def __init__(self, model_dir: Path, manifest: dict):
        self.manifest = manifest
        self.version = manifest["model_version"]
        self.categories = manifest["categories"]
        self.platt = manifest["calibration"]
        self.booster = xgb.Booster()
        self.booster.load_model(str(model_dir / "model.json"))
        self.nthread = 1  # chấm từng giao dịch: 1 luồng là nhanh nhất
        self.booster.set_param({"nthread": self.nthread})
        # dừng sớm: chỉ dùng số cây tốt nhất, giống lúc hiệu chỉnh và chọn ngưỡng
        self.iters = (0, int(manifest["params"]["n_estimators_used"]))

    def frame(self, rows: list[dict] | pd.DataFrame) -> np.ndarray:
        return to_model_frame(rows, self.categories)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        raw = self.booster.predict(dmatrix(X, self.nthread), iteration_range=self.iters)
        return raw, platt_apply(raw, self.platt["a"], self.platt["b"])

    def explain(self, X: np.ndarray, top: int = 5) -> list[list[dict]]:
        """Mức đóng góp của từng feature (TreeSHAP có sẵn trong XGBoost), theo thang log-odds."""
        contrib = self.booster.predict(dmatrix(X, self.nthread), pred_contribs=True, iteration_range=self.iters)[:, :-1]
        out = []
        for i in range(contrib.shape[0]):
            idx = np.argsort(-np.abs(contrib[i]))[:top]
            out.append([{"feature": FEATURES[j], "value": self._value(FEATURES[j], X[i, j]),
                         "contribution": round(float(contrib[i, j]), 4),
                         "direction": "tăng rủi ro" if contrib[i, j] > 0 else "giảm rủi ro"} for j in idx])
        return out

    def _value(self, name: str, v):
        if name in CATEGORICAL:
            return category_name(float(v), self.categories)
        return _jsonable(v)


def _jsonable(v):
    if isinstance(v, (np.floating, float)):
        return None if math.isnan(float(v)) else round(float(v), 4)
    if isinstance(v, (np.integer,)):
        return int(v)
    return None if (isinstance(v, float) and math.isnan(v)) else str(v)
