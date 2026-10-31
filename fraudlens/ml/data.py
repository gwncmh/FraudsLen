"""Nạp Sparkov, đi qua đúng ranh giới PII như online, chia tập theo thời gian, cache để chạy lại nhanh."""
from __future__ import annotations

import hashlib
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from fraudlens.common import pii
from fraudlens.common.features import build_offline

USECOLS = ["trans_date_trans_time", "cc_num", "merchant", "category", "amt", "state", "lat", "long",
           "city_pop", "dob", "trans_num", "merch_lat", "merch_long", "is_fraud"]


def load_raw(data_dir: Path) -> pd.DataFrame:
    parts = []
    for name, src in (("fraudTrain.csv", "train_file"), ("fraudTest.csv", "test_file")):
        p = data_dir / name
        if not p.exists():
            raise FileNotFoundError(f"Thiếu {p}. Tải bộ Sparkov từ Kaggle (kartik2112/fraud-detection) vào {data_dir}/")
        d = pd.read_csv(p, usecols=USECOLS, dtype={"cc_num": str, "trans_num": str, "zip": str})
        d["source_file"] = src
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def prepare(raw: pd.DataFrame, key: bytes, sample_cards: float = 1.0, seed: int = 42) -> pd.DataFrame:
    """Áp dụng đúng pii.minimize() như API cho từng dòng -> offline và online giống hệt nhau."""
    if sample_cards < 1.0:
        cards = raw["cc_num"].unique()
        rng = np.random.default_rng(seed)
        keep = set(rng.choice(cards, size=max(1, int(len(cards) * sample_cards)), replace=False))
        raw = raw[raw["cc_num"].isin(keep)]
    recs = [pii.minimize(r, key) for r in raw.to_dict("records")]
    df = pd.DataFrame(recs, index=raw.index)
    df["is_fraud"] = raw["is_fraud"].astype(int).values
    df["source_file"] = raw["source_file"].values
    df = df.sort_values(["event_ts", "trans_num"], kind="mergesort").reset_index(drop=True)
    return df


def temporal_split(df: pd.DataFrame, frac_val_a: float = 0.125, frac_val_b: float = 0.125) -> pd.Series:
    """test = toàn bộ fraudTest.csv (protocol chung). fraudTrain.csv chia theo thời gian:
    train (sớm nhất) -> val_A (chọn model, hiệu chỉnh, ngưỡng) -> val_B (kiểm tra lại)."""
    split = pd.Series("test", index=df.index)
    tr = df.index[df["source_file"] == "train_file"]  # df đã sắp theo thời gian
    n = len(tr)
    a0 = int(n * (1 - frac_val_a - frac_val_b))
    b0 = int(n * (1 - frac_val_b))
    split.loc[tr[:a0]] = "train"
    split.loc[tr[a0:b0]] = "val_a"
    split.loc[tr[b0:]] = "val_b"
    return split


def load_prepared(data_dir: Path, key: bytes, sample_cards: float, cache_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trả về (df đã tối thiểu hóa + cột split, bảng feature). Cache theo (mẫu, khóa) để lần sau nhanh."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha256(key + f"|{sample_cards}".encode()).hexdigest()[:12]
    p = cache_dir / f"prepared_{tag}.pkl"
    if p.exists():
        with open(p, "rb") as f:
            return pickle.load(f)
    print("  nạp CSV và tối thiểu hóa dữ liệu (lần đầu mất vài phút)...")
    df = prepare(load_raw(data_dir), key, sample_cards)
    df["split"] = temporal_split(df)
    print(f"  tính feature cho {len(df):,} giao dịch bằng hàm dùng chung (lần đầu mất vài phút)...")
    feats = build_offline(df)
    with open(p, "wb") as f:
        pickle.dump((df, feats), f, protocol=pickle.HIGHEST_PROTOCOL)
    return df, feats
