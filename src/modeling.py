"""
Step 4 - Modelling: scaling, PCA, K-Means, DBSCAN, anomaly scoring
==================================================================

StandardScaler
    K-Means and DBSCAN measure *distance*. If one feature ranges 0-3000 (cards)
    and another 0-1 (shares), the big one dominates every distance. Scaling
    each feature to mean 0 / std 1 gives all features an equal vote.

PCA (Principal Component Analysis)
    Rotates the 7 scaled features into new axes ordered by how much variance
    they explain. The first 2-3 components let us *see* the clusters on a plot.
    We cluster on the full scaled features and use PCA for visualisation and
    for DBSCAN (density works better in fewer dimensions).

K-Means
    Picks k centres and assigns each shop to the nearest one, repeating until
    stable. We choose k with:
      * Elbow curve - inertia (within-cluster spread) vs k; look for the bend.
      * Silhouette score - how much closer a point is to its own cluster than
        to the next one (-1 to 1, higher is better).

DBSCAN
    Groups points that sit in dense regions. A point with fewer than
    `min_samples` neighbours within radius `eps`, and not reachable from a
    dense region, is labelled -1 = noise. Those are shops that behave unlike
    any common pattern -> outlier candidates.

Peer-deviation score
    For fraud screening we compare each shop to its own cluster: a robust
    z-score (median / MAD) of utilization ratio and rice-per-transaction
    within the cluster. |z| > 3.5 means the shop is far from its peers.
"""
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from config import RANDOM_STATE
from features import MODEL_FEATURES

CLIP_FEATURES = [c + "_clip" for c in MODEL_FEATURES]


def scale(df: pd.DataFrame):
    scaler = StandardScaler()
    X = scaler.fit_transform(df[CLIP_FEATURES])
    return X, scaler


def fit_pca(X: np.ndarray, n_components: int = 3):
    pca = PCA(n_components=n_components, random_state=RANDOM_STATE)
    Z = pca.fit_transform(X)
    return Z, pca


def k_search(X: np.ndarray, k_range=range(2, 11), sample: int = 15000) -> pd.DataFrame:
    """Elbow + silhouette for each k. Silhouette is O(n^2) so we score a sample."""
    rng = np.random.default_rng(RANDOM_STATE)
    idx = rng.choice(len(X), size=min(sample, len(X)), replace=False)
    rows = []
    for k in k_range:
        km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE).fit(X)
        rows.append({
            "k": k,
            "inertia": km.inertia_,
            "silhouette": silhouette_score(X[idx], km.labels_[idx]),
        })
    return pd.DataFrame(rows)


def fit_kmeans(X: np.ndarray, k: int) -> KMeans:
    return KMeans(n_clusters=k, n_init=20, random_state=RANDOM_STATE).fit(X)


def k_distance(Z: np.ndarray, min_samples: int) -> np.ndarray:
    """Sorted distance to the min_samples-th neighbour. The 'knee' suggests eps."""
    nn = NearestNeighbors(n_neighbors=min_samples).fit(Z)
    d, _ = nn.kneighbors(Z)
    return np.sort(d[:, -1])


def fit_dbscan(Z: np.ndarray, min_samples: int, quantile: float = 0.98):
    """eps = the 98th percentile of k-distances, i.e. the point where the curve bends up."""
    eps = float(np.quantile(k_distance(Z, min_samples), quantile))
    labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(Z)
    return labels, eps


def robust_z(s: pd.Series) -> pd.Series:
    med = s.median()
    mad = (s - med).abs().median()
    if mad == 0 or np.isnan(mad):
        return pd.Series(0.0, index=s.index)
    return 0.6745 * (s - med) / mad


def peer_deviation(df: pd.DataFrame, cluster_col: str = "cluster") -> pd.DataFrame:
    out = df.copy()
    for f in ["utilization_ratio", "rice_per_trans"]:
        out[f"z_{f}"] = out.groupby(cluster_col)[f].transform(robust_z)
    out["peer_z_max"] = out[["z_utilization_ratio", "z_rice_per_trans"]].abs().max(axis=1)
    return out


def name_personas(profile: pd.DataFrame, urban_pct: pd.Series) -> dict:
    """Give each K-Means cluster a readable persona from its feature means.

    Each cluster is named after the feature that makes it most different from
    the others (largest standardized gap from the mean of cluster means). The
    rules compare clusters with each other, not with fixed thresholds, so the
    names still make sense if the data or k changes.
    """
    z = (profile - profile.mean()) / profile.std(ddof=0).replace(0, 1)
    names = {}
    taken = set()

    def pick(cands):
        for c in cands:
            if c not in taken:
                taken.add(c)
                return c
        return None

    # 1. Mostly-urban cluster with the highest portability = metro hubs
    c = pick(urban_pct[urban_pct > 50].sort_values(ascending=False).index)
    if c is not None:
        names[c] = "Urban Portability Hubs"
    # 2. Most volatile, least utilized = erratic shops
    c = pick((z["trans_cv"] - z["utilization_ratio"]).sort_values(ascending=False).index)
    names[c] = "Volatile Under-Utilized"
    # 3. Highest Antyodaya share = poorest catchments
    c = pick(z["aay_share"].sort_values(ascending=False).index)
    names[c] = "Antyodaya-Heavy Small Shops"
    # 4. Next highest portability = towns with heavy ONORC use
    c = pick(z["portability_share"].sort_values(ascending=False).index)
    names[c] = "Portability-Active Towns"
    # 5. Whatever is left is the typical shop
    rest = [c for c in profile.index if c not in names]
    for i, c in enumerate(rest):
        names[c] = "Steady Rural Core" if i == 0 else f"Steady Rural Core {i + 1}"
    return names
