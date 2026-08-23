"""
Baseline ML model(s) — XGBoost/LightGBM/RandomForest/LogisticRegression wrappers.

These models predict BUY / SELL / NO-TRADE probabilities from engineered
features. The LLM layer is NOT used for numerical predictions — it only
summarizes the structured outputs into human-readable reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer


try:
    import xgboost as xgb  # type: ignore
    HAS_XGB = True
except Exception:
    HAS_XGB = False


MODEL_REGISTRY: Dict[str, type] = {}


def register_model(name: str):
    def deco(cls):
        MODEL_REGISTRY[name] = cls
        return cls
    return deco


@dataclass
class ModelPrediction:
    proba_buy: float
    proba_sell: float
    proba_no_trade: float
    label: str           # "BUY" / "SELL" / "NO_TRADE"
    confidence: int      # 0-100


class BaseFXModel:
    name: str = "base"

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        self.params = params or {}
        self.pipeline: Optional[Pipeline] = None
        self._feature_names: List[str] = []

    def _build(self) -> Pipeline:
        raise NotImplementedError

    def fit(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, Any]:
        self._feature_names = list(X.columns)
        self.pipeline = self._build()
        self.pipeline.fit(X, y)
        return {"n_samples": len(X), "features": self._feature_names}

    def predict(self, X: pd.DataFrame) -> ModelPrediction:
        if self.pipeline is None:
            raise RuntimeError("Model not fitted")
        probs = self.pipeline.predict_proba(X[self._feature_names].tail(1))[0]
        classes = list(self.pipeline.steps[-1][1].classes_)
        p_buy = float(probs[classes.index(1)]) if 1 in classes else 0.0
        p_sell = float(probs[classes.index(-1)]) if -1 in classes else 0.0
        p_none = float(probs[classes.index(0)]) if 0 in classes else 1.0
        # Normalize
        total = p_buy + p_sell + p_none
        if total > 0:
            p_buy, p_sell, p_none = p_buy / total, p_sell / total, p_none / total

        # Decision threshold: need p(signal) > p(none) AND confidence >= 55
        if p_buy > p_sell and p_buy > p_none:
            label = "BUY"; conf = int(p_buy * 100)
        elif p_sell > p_buy and p_sell > p_none:
            label = "SELL"; conf = int(p_sell * 100)
        else:
            label = "NO_TRADE"; conf = int(p_none * 100)
        return ModelPrediction(p_buy, p_sell, p_none, label, conf)

    def evaluate(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, Any]:
        preds = self.pipeline.predict(X[self._feature_names])
        return {
            "accuracy": float(accuracy_score(y, preds)),
            "report": classification_report(y, preds, output_dict=True, zero_division=0),
        }


@register_model("logistic")
class LogisticModel(BaseFXModel):
    name = "logistic"

    def _build(self) -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, multi_class="multinomial",
                                        class_weight="balanced", C=0.1)),
        ])


@register_model("random_forest")
class RandomForestModel(BaseFXModel):
    name = "random_forest"

    def _build(self) -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("clf", RandomForestClassifier(
                n_estimators=self.params.get("n_estimators", 300),
                max_depth=self.params.get("max_depth", 8),
                min_samples_leaf=20,
                class_weight="balanced_subsample",
                n_jobs=-1,
                random_state=42,
            )),
        ])


@register_model("gradient_boosting")
class GradientBoostingModel(BaseFXModel):
    name = "gradient_boosting"

    def _build(self) -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("clf", GradientBoostingClassifier(
                n_estimators=self.params.get("n_estimators", 200),
                max_depth=self.params.get("max_depth", 5),
                learning_rate=self.params.get("lr", 0.05),
                subsample=0.8,
                random_state=42,
            )),
        ])


if HAS_XGB:
    @register_model("xgboost")
    class XGBoostModel(BaseFXModel):
        name = "xgboost"

        def _build(self) -> Pipeline:
            return Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("clf", xgb.XGBClassifier(
                    n_estimators=self.params.get("n_estimators", 300),
                    max_depth=self.params.get("max_depth", 6),
                    learning_rate=self.params.get("lr", 0.05),
                    subsample=0.8,
                    colsample_bytree=0.8,
                    objective="multi:softprob",
                    num_class=3,
                    eval_metric="mlogloss",
                    use_label_encoder=False,
                    random_state=42,
                    n_jobs=-1,
                )),
            ])


def get_model(name: str, params: Optional[Dict[str, Any]] = None) -> BaseFXModel:
    if name not in MODEL_REGISTRY:
        raise KeyError(f"Unknown model '{name}'. Available: {list(MODEL_REGISTRY)}")
    return MODEL_REGISTRY[name](params)


def available_models() -> List[str]:
    return list(MODEL_REGISTRY.keys())
