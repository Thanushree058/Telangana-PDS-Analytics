"""
Telangana PDS Shop Intelligence - Streamlit dashboard

    streamlit run app.py

Run `python src/run_pipeline.py` once before launching, so that
data/processed/ holds the model outputs.

Filtering happens in the database, not in pandas: every sidebar choice
is sent to MySQL (or the local SQLite fallback) as a parameterised SQL
query, and only the matching rows come back. See src/db.py.
"""
import json
import sys
from pathlib import Path

import folium
import pandas as pd
import plotly.express as px
import streamlit as st
from folium.plugins import HeatMap
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT / "src"))
from config import PROCESSED_DIR  # noqa: E402
from db import PDSData, connect  # noqa: E402

st.set_page_config(page_title="Telangana PDS Shop Intelligence", page_icon="🌾", layout="wide")

PERSONA_COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#9333ea", "#0891b2", "#db2777", "#65a30d"]
ANOMALY_COLOR = "#dc2626"
FEATURE_LABELS = {
    "utilization_ratio": "Utilization (trans / cards)",
    "portability_share": "Portability share",
    "rice_per_trans": "Rice kg per transaction",
    "wheat_share": "Wheat share",
    "aay_share": "Antyodaya card share",
    "trans_cv": "Monthly volatility (CV)",
    "active_month_share": "Active-month share",
}


# cache_resource: one database connection pool shared by every rerun and user.
@st.cache_resource(show_spinner="Connecting to the database...")
def get_db():
    engine, label, note = connect()
    return PDSData(engine), label, note


# cache_data: remember each query's answer for 10 minutes, keyed by its
# arguments, so flipping back to a filter you already used is instant.
# The `source` argument keeps MySQL and SQLite answers apart.
@st.cache_data(ttl=600, show_spinner=False)
def q(method: str, source: str, *args):
    return getattr(db, method)(*args)


@st.cache_data
def load_model_meta():
    meta = json.loads((PROCESSED_DIR / "model_meta.json").read_text())
    ks = pd.read_csv(PROCESSED_DIR / "k_search.csv")
    return meta, ks


try:
    db, source, source_note = get_db()
except Exception as e:
    st.error(str(e))
    st.stop()

meta, ks = load_model_meta()
opts = q("filter_options", source)
personas = opts["personas"]
years = opts["years"]
color_of = {p: PERSONA_COLORS[i % len(PERSONA_COLORS)] for i, p in enumerate(personas)}

# ---------------- Sidebar filters ----------------
st.sidebar.title("🌾 Filters")
year = st.sidebar.selectbox("Year", years, index=len(years) - 1)
sel_districts = tuple(st.sidebar.multiselect("District", opts["districts"], placeholder="All districts"))
sel_personas = tuple(st.sidebar.multiselect("Persona", personas, placeholder="All personas"))
only_anoms = st.sidebar.checkbox("Only flagged anomalies")
st.sidebar.caption(
    "Note: 2025 covers Jan to May only. Ratios stay comparable, but totals are partial."
)

# The database does the filtering; we get back only the matching shops.
view = q("shops", source, year, sel_districts, sel_personas, only_anoms)

st.sidebar.divider()
if source.startswith("MySQL"):
    st.sidebar.success(f"Data source: **{source}**")
else:
    st.sidebar.warning(f"Data source: **{source}**")
    if source_note:
        st.sidebar.caption(source_note)
with st.sidebar.expander("🔍 SQL behind this view"):
    sql, params, _ = db.shops_query(year, sel_districts, sel_personas, only_anoms)
    st.code(sql, language="sql")
    st.caption(f"Parameters (sent separately, never pasted into the SQL): `{params}`")
    st.caption(f"Rows returned: {len(view):,}")

# ---------------- Header & KPIs ----------------
st.title("Telangana PDS: Shop Performance Clusters & Anomalies")
st.caption(
    f"Fair Price Shop behaviour, clustered with K-Means (k={meta['k']}) and screened with DBSCAN "
    "and peer-deviation scores. Source: Telangana Open Data Portal, 2023-2025."
)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Shops in view", f"{len(view):,}")
c2.metric("Avg utilization", f"{view['utilization_ratio'].median():.2f}" if len(view) else "-")
c3.metric("Portability share", f"{(view['portability_trans'].sum() / max(view['total_trans'].sum(), 1)):.1%}")
c4.metric("Flagged anomalies", f"{int(view['anomaly_flag'].sum()):,}")
c5.metric("Portability hubs", f"{int(view['portability_hub'].sum()):,}")

tab_map, tab_clusters, tab_trends, tab_shop, tab_anom = st.tabs(
    ["🗺️ Map", "🧩 Clusters", "📈 Trends & policy", "🔎 Shop lookup", "🚩 Anomalies"]
)

# ---------------- Map ----------------
with tab_map:
    left, right = st.columns([3, 1])
    with right:
        layer = st.radio("Layer", ["Shops by persona", "Anomaly hotspots (heatmap)"])
        max_pts = st.slider("Max shops drawn", 500, 6000, 2500, step=500,
                            help="Drawing thousands of markers slows the browser. Filter by district to see all.")
    pts = view.dropna(subset=["latitude", "longitude"])
    with left:
        if pts.empty:
            st.info("No shops with coordinates for these filters.")
        else:
            m = folium.Map(location=[pts["latitude"].mean(), pts["longitude"].mean()],
                           zoom_start=7 if not sel_districts else 9, tiles="OpenStreetMap")
            if layer.startswith("Shops"):
                # Always draw every anomaly, sample the rest.
                anom = pts[pts["anomaly_flag"]]
                rest = pts[~pts["anomaly_flag"]]
                rest = rest.sample(min(len(rest), max(max_pts - len(anom), 0)), random_state=0)
                for _, r in pd.concat([rest, anom]).iterrows():
                    flagged = r["anomaly_flag"]
                    folium.CircleMarker(
                        [r["latitude"], r["longitude"]],
                        radius=5 if flagged else 3,
                        color=ANOMALY_COLOR if flagged else color_of[r["persona"]],
                        weight=1.5 if flagged else 0,
                        fill=True, fill_color=color_of[r["persona"]], fill_opacity=0.8,
                        tooltip=(f"Shop {r['shopNo']} · {r['distName']}<br>{r['persona']}"
                                 f"<br>Utilization {r['utilization_ratio']:.2f} · "
                                 f"Portability {r['portability_share']:.1%}"
                                 + ("<br><b>⚠ flagged</b>" if flagged else "")),
                    ).add_to(m)
            else:
                hot = pts[pts["anomaly_flag"] | pts["portability_hub"]]
                HeatMap(hot[["latitude", "longitude"]].values.tolist(), radius=12, blur=15).add_to(m)
            st_folium(m, height=560, use_container_width=True, returned_objects=[])
    with right:
        st.markdown("**Legend**")
        for p in personas:
            st.markdown(f"<span style='color:{color_of[p]}'>●</span> {p}", unsafe_allow_html=True)
        st.markdown(f"<span style='color:{ANOMALY_COLOR}'>◯</span> red ring = flagged anomaly",
                    unsafe_allow_html=True)

# ---------------- Clusters ----------------
with tab_clusters:
    a, b = st.columns(2)
    with a:
        st.subheader("PCA view of shop behaviour")
        ev = meta["pca_explained_variance"]
        sample = view.sample(min(len(view), 8000), random_state=0)
        fig = px.scatter(sample, x="pc1", y="pc2", color="persona", color_discrete_map=color_of,
                         opacity=0.55, hover_data=["shopNo", "distName"],
                         labels={"pc1": f"PC1 ({ev[0]:.0%} var)", "pc2": f"PC2 ({ev[1]:.0%} var)"})
        fig.update_traces(marker_size=4)
        st.plotly_chart(fig, width="stretch")
    with b:
        st.subheader("Choosing k")
        fig = px.line(ks, x="k", y="silhouette", markers=True, title="Silhouette score (higher is better)")
        fig.add_vline(x=meta["k"], line_dash="dash")
        st.plotly_chart(fig, width="stretch")
        fig = px.line(ks, x="k", y="inertia", markers=True, title="Elbow curve (inertia)")
        fig.add_vline(x=meta["k"], line_dash="dash")
        st.plotly_chart(fig, width="stretch")

    st.subheader("Persona profiles (mean of each feature)")
    prof = view.groupby("persona").agg(
        shops=("shopNo", "size"),
        urban_pct=("area_type", lambda s: (s == "Urban").mean() * 100),
        avg_cards=("avg_cards", "mean"),
        **{v: (k, "mean") for k, v in FEATURE_LABELS.items()},
        anomaly_pct=("anomaly_flag", lambda s: s.mean() * 100),
    )
    st.dataframe(prof.style.format(precision=2).background_gradient(axis=0, cmap="Blues"),
                 width="stretch")

    mix = view.groupby(["area_type", "persona"]).size().reset_index(name="shops")
    mix["pct"] = mix["shops"] / mix.groupby("area_type")["shops"].transform("sum") * 100
    fig = px.bar(mix, x="area_type", y="pct", color="persona", color_discrete_map=color_of,
                 labels={"pct": "% of shops", "area_type": ""},
                 title="Persona mix: Urban vs Rural (cluster purity check)")
    st.plotly_chart(fig, width="stretch")

# ---------------- Trends ----------------
with tab_trends:
    t = q("monthly_trend", source, sel_districts, sel_personas, only_anoms)
    st.caption("Follows the District, Persona and anomaly filters. The Year filter doesn't apply "
               "here because these charts show every month. Totals are summed inside the database.")
    st.subheader("One Nation One Ration Card: portability over time")
    fig = px.line(t, x="date", y="portability_share", color="area_type", markers=True,
                  labels={"portability_share": "Share of transactions by other-shop cards", "date": ""})
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig, width="stretch")

    a, b = st.columns(2)
    with a:
        st.subheader("Monthly transactions (seasonality)")
        tt = t.groupby("date")["trans"].sum().reset_index()
        fig = px.bar(tt, x="date", y="trans", labels={"trans": "Transactions", "date": ""})
        st.plotly_chart(fig, width="stretch")
    with b:
        st.subheader("How shops moved between personas")
        y0, y1 = years[0], years[-1]
        area = st.radio("Area", ["Urban", "Rural"], horizontal=True)
        moves = q("persona_transitions", source, y0, y1, area, sel_districts)
        if moves.empty:
            st.info("No shops present in both years for these filters.")
        else:
            tr = moves.pivot_table(index="persona_from", columns="persona_to", values="shops",
                                   aggfunc="sum", fill_value=0)
            tr = tr.div(tr.sum(axis=1), axis=0) * 100
            fig = px.imshow(tr.round(1), text_auto=True, color_continuous_scale="Blues",
                            labels=dict(x=f"Persona in {y1}", y=f"Persona in {y0}", color="% of shops"))
            st.plotly_chart(fig, width="stretch")

# ---------------- Shop lookup ----------------
with tab_shop:
    shop_ids = q("shop_numbers", source, year)
    shop = st.selectbox("Search shop number", shop_ids, index=None, placeholder="Type a shopNo, e.g. 1901001")
    row = q("shop", source, year, shop) if shop is not None else None
    if row is not None:
        peer_avg = q("peer_averages", source, year, row["persona"], tuple(FEATURE_LABELS))
        st.markdown(
            f"**Shop {shop}** · {row['officeName']}, {row['distName']} ({row['area_type']}) · "
            f"Persona: **{row['persona']}** · "
            + ("🚩 **Flagged**" if row["anomaly_flag"] else "✅ Not flagged")
        )
        comp = pd.DataFrame({
            "feature": list(FEATURE_LABELS.values()),
            "This shop": [row[k] for k in FEATURE_LABELS],
            "Cluster average": [peer_avg[k] for k in FEATURE_LABELS],
        })
        # Features have different units (kg vs shares), so plot the shop as an
        # index where the cluster average = 100.
        comp["index"] = comp["This shop"] / comp["Cluster average"].replace(0, float("nan")) * 100
        a, b = st.columns(2)
        with a:
            fig = px.bar(comp, y="feature", x="index", orientation="h", text_auto=".0f",
                         color=comp["index"].sub(100).abs() > 50,
                         color_discrete_map={True: "#dc2626", False: "#64748b"},
                         labels={"index": "Shop value as % of cluster average", "feature": "", "color": "> ±50%"},
                         title="Shop vs its cluster average (100 = typical for its persona)")
            fig.add_vline(x=100, line_dash="dash")
            st.plotly_chart(fig, width="stretch")
            st.dataframe(comp.set_index("feature")[["This shop", "Cluster average"]].style.format(precision=3),
                         width="stretch")
        with b:
            hist = q("shop_history", source, row["distCode"], shop)
            fig = px.line(hist, x="date", y=["noOfTrans", "totalRcs", "otherShopTransCnt"], markers=True,
                          title="Monthly history",
                          labels={"value": "Count", "date": "", "variable": ""})
            st.plotly_chart(fig, width="stretch")
        st.caption(f"Peer z-scores: utilization {row['z_utilization_ratio']:.1f}, "
                   f"rice per transaction {row['z_rice_per_trans']:.1f}. Beyond ±3.5 counts as unusual "
                   f"for this persona. DBSCAN noise: {'yes' if row['dbscan_noise'] else 'no'}.")

# ---------------- Anomalies ----------------
with tab_anom:
    st.subheader("Shops that deviate from their peers")
    st.markdown(
        "- **DBSCAN noise**: the shop sits outside every dense behaviour pattern.\n"
        "- **Peer outlier**: utilization or rice-per-transaction is more than 3.5 robust "
        "standard deviations from its own persona. Very high utilization can mean repeated draws "
        "on the same cards (possible ghost beneficiaries); very high rice per transaction can mean diversion."
    )
    cols = ["shopNo", "distName", "officeName", "persona", "avg_cards", "avg_monthly_trans",
            "utilization_ratio", "rice_per_trans", "portability_share",
            "z_utilization_ratio", "z_rice_per_trans", "dbscan_noise", "peer_outlier"]
    an = view[view["anomaly_flag"]].sort_values("peer_z_max", ascending=False)[cols]
    st.dataframe(an.style.format(precision=2), width="stretch", height=420)
    st.download_button("Download flagged shops (CSV)", an.to_csv(index=False), f"flagged_shops_{year}.csv")

    st.subheader("Portability hubs: candidates for more frequent restocking")
    hubs = view[view["portability_hub"]].sort_values("portability_trans", ascending=False)
    st.dataframe(hubs[["shopNo", "distName", "officeName", "avg_monthly_trans", "portability_share",
                       "portability_trans"]].style.format(precision=2),
                 width="stretch", height=300)
