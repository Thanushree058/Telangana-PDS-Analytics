"""
End-to-end pipeline: master data -> features -> models -> outputs for the app.

    python src/run_pipeline.py            # uses K from config (BEST_K) or picks by silhouette
    python src/run_pipeline.py --k 5

Outputs (data/processed/):
    master_monthly.parquet     shop-month master table
    shop_year_clusters.parquet one row per shop-year with features, PCA, cluster, DBSCAN, flags
    cluster_profiles.csv       mean feature values per persona
    k_search.csv               elbow + silhouette table
    anomalies.csv              shops flagged by DBSCAN and/or peer deviation
"""
import argparse
import json

import pandas as pd

import data_prep
import features
import modeling
from config import PROCESSED_DIR

PEER_Z_THRESHOLD = 3.5
DEFAULT_K = 5


def run(k: int | None = None) -> None:
    master_path = PROCESSED_DIR / "master_monthly.parquet"
    if not master_path.exists():
        data_prep.main()
    master = pd.read_parquet(master_path)

    feats = features.shop_year_features(master)
    df = features.model_ready(feats)
    print(f"Shop-years for modelling: {len(df):,} (of {len(feats):,})")

    X, _ = modeling.scale(df)
    Z, pca = modeling.fit_pca(X, n_components=3)
    df[["pc1", "pc2", "pc3"]] = Z

    ks = modeling.k_search(X)
    ks.to_csv(PROCESSED_DIR / "k_search.csv", index=False)
    print(ks.round(3).to_string(index=False))
    k = k or DEFAULT_K

    km = modeling.fit_kmeans(X, k)
    df["cluster"] = km.labels_

    min_samples = 2 * Z.shape[1]
    df["dbscan_label"], eps = modeling.fit_dbscan(Z, min_samples=min_samples)
    df["dbscan_noise"] = df["dbscan_label"] == -1

    profile = df.groupby("cluster")[features.MODEL_FEATURES].mean()
    urban_pct = df.groupby("cluster")["area_type"].apply(lambda s: (s == "Urban").mean() * 100)
    personas = modeling.name_personas(profile, urban_pct)
    df["persona"] = df["cluster"].map(personas)

    df = modeling.peer_deviation(df)
    df["peer_outlier"] = df["peer_z_max"] > PEER_Z_THRESHOLD
    df["anomaly_flag"] = df["dbscan_noise"] | df["peer_outlier"]

    # Portability hub = top 5% portability share among shops with real volume
    thresh = df.loc[df["avg_monthly_trans"] >= 200, "portability_share"].quantile(0.95)
    df["portability_hub"] = (df["portability_share"] >= thresh) & (df["avg_monthly_trans"] >= 200)

    df.to_parquet(PROCESSED_DIR / "shop_year_clusters.parquet", index=False)

    prof = df.groupby("persona").agg(
        shops=("shopNo", "size"),
        urban_pct=("area_type", lambda s: (s == "Urban").mean() * 100),
        avg_cards=("avg_cards", "mean"),
        avg_monthly_trans=("avg_monthly_trans", "mean"),
        **{f: (f, "mean") for f in features.MODEL_FEATURES if f != "log_avg_cards"},
        dbscan_noise_pct=("dbscan_noise", lambda s: s.mean() * 100),
        anomaly_pct=("anomaly_flag", lambda s: s.mean() * 100),
    ).round(3)
    prof.to_csv(PROCESSED_DIR / "cluster_profiles.csv")
    print(prof.to_string())

    cols = ["distName", "officeName", "shopNo", "year", "persona", "avg_cards", "avg_monthly_trans",
            "utilization_ratio", "rice_per_trans", "portability_share", "z_utilization_ratio",
            "z_rice_per_trans", "dbscan_noise", "peer_outlier"]
    anomalies = df[df["anomaly_flag"]].sort_values("peer_z_max", ascending=False)[cols]
    anomalies.to_csv(PROCESSED_DIR / "anomalies.csv", index=False)

    meta = {
        "k": k,
        "silhouette_at_k": float(ks.loc[ks["k"] == k, "silhouette"].iloc[0]),
        "pca_explained_variance": pca.explained_variance_ratio_.round(4).tolist(),
        "pca_loadings": pd.DataFrame(pca.components_, columns=features.MODEL_FEATURES,
                                     index=["pc1", "pc2", "pc3"]).round(3).to_dict(),
        "dbscan_eps": eps,
        "dbscan_min_samples": min_samples,
        "dbscan_noise_count": int(df["dbscan_noise"].sum()),
        "peer_outlier_count": int(df["peer_outlier"].sum()),
        "portability_hub_threshold": float(thresh),
        "personas": {int(c): n for c, n in personas.items()},
    }
    (PROCESSED_DIR / "model_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps({k_: v for k_, v in meta.items() if k_ != "pca_loadings"}, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=None)
    run(ap.parse_args().k)
