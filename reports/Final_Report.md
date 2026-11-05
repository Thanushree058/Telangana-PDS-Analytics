# Final Report: Telangana PDS Shop Performance Clustering and Anomaly Profiling

## 1. Objective
Segment Telangana's Fair Price Shops by operational behaviour, flag shops that deviate from their peers, and
answer three questions for the Civil Supplies Department: how One Nation One Ration Card (ONORC) has changed
shop behaviour, which shops look suspicious, and which shops need more frequent restocking.

## 2. Data
| Source (Telangana Open Data Portal) | Files | Grain |
|---|---|---|
| FP Shop Wise Transactions | 29 monthly CSVs, Jan 2023 to May 2025 | shop × month |
| Fair Price Shop Card Status | 30 monthly CSVs, Jan 2023 to Jun 2025 | shop × month |
| FPS Latitude/Longitude | 1 snapshot (July 2025) | shop |

After integration: **499,648 shop-months, 17,491 shops, 33 districts**. Data quality: 0.03% of shop-months
lack a card count, and 1.6% of shops lack valid coordinates. 2025 is partial (5 months), so years are compared
using ratios.

## 3. Key EDA findings
* **Portability is large and still growing.** It rose from 34.4% (2023) to 36.0% (2024) and 37.5% (2025) of all
  transactions. Urban (Hyderabad, Medchal, Ranga Reddy): 64.7% → 68.0%. Rural: 26.3% → 28.8%.
  *(fig 01)*
* **Entitlement drives volume.** Cards and transactions correlate strongly, and most shops sit just below one
  transaction per card per month. The median shop-year utilization is 0.84. *(fig 02)*
* **Rice dominates.** Wheat is distributed by a minority of shops, mostly urban, which makes commodity mix a
  strong urban marker. *(fig 03-04)*
* **Demand is flat across the year** (every month within ±2.5% of the yearly average), because rations are a
  fixed monthly entitlement. A spike at a single shop is therefore itself a warning sign. *(fig 05)*

## 4. Feature selection (justification)
| Feature | Business question it answers |
|---|---|
| Utilization ratio (transactions / cards) | ghost beneficiaries (too high), exclusion or low uptake (too low) |
| Portability share | ONORC impact, restocking hubs |
| Rice per transaction | diversion or over-issue |
| Wheat share (bounded rice-to-wheat intensity) | commodity mix, urban profile |
| Antyodaya card share | vulnerability of the catchment |
| Monthly volatility (CV) | irregular operations |
| log(cards) | shop size, log-scaled |

`active_month_share` was dropped because it has near-zero variance (almost every shop is active every month).
No feature pair has |r| > 0.9. All features were clipped at the 0.1/99.9 percentiles and standardized.

## 5. Model results
* **PCA:** PC1 (30%) is the urban-hub axis, PC2 (21%) the poverty / ration-size axis, PC3 (19%) the
  irregularity axis. Together they explain 70% of the variance.
* **K-Means:** k = 5 (silhouette 0.250, the best value in the 4-5 range the brief asks for. k = 2 scores 0.48 but
  only separates metro hubs from everything else).

| Persona | Shop-years | Urban % | Utilization | Portability | Rice kg/trans | AAY share | Volatility | Flag rate |
|---|---|---|---|---|---|---|---|---|
| Steady Rural Core | 26,623 | 3% | 0.80 | 13% | 19.4 | 5.9% | 0.04 | 0.4% |
| Portability-Active Towns | 10,885 | 11% | 0.97 | 59% | 20.5 | 5.4% | 0.07 | 5.1% |
| Antyodaya-Heavy Small Shops | 8,975 | 11% | 0.82 | 13% | 23.1 | 13.1% | 0.04 | 3.1% |
| Urban Portability Hubs | 3,514 | 100% | 0.99 | 78% | 22.0 | 3.5% | 0.09 | 2.8% |
| Volatile Under-Utilized | 1,907 | 12% | 0.54 | 55% | 20.2 | 6.0% | 0.37 | 19.9% |

* **DBSCAN** (min_samples = 6, eps = 0.45 from the k-distance knee): 621 noise shop-years (1.2%), and 20% of the
  Volatile persona.
* **Peer deviation** (robust z > 3.5 within persona): 912 shop-years.
* **Total flagged:** 1,422 shop-years (2.7%). The full ranked list is in `data/processed/anomalies.csv`.

## 6. Evaluation
* **Silhouette and elbow:** see fig 08. The elbow flattens after k ≈ 5.
* **Cluster purity vs Urban/Rural:** 94% overall. The *Urban Portability Hubs* persona is 100% urban even though
  area type was never given to the model. That's independent evidence the features capture real structure.
* **Stability:** 85-98% of shops keep their persona from 2023 to 2024 (fig 15), so personas reflect lasting
  behaviour, not monthly noise.
* **Dashboard:** the data lives in MySQL (`telangana_pds`, star schema). Every filter becomes a parameterised
  SQL query, so the database does the filtering and aggregation and returns only the rows the charts need.
  Composite indexes on `(year, persona)` and `(year, anomaly_flag)` keep typical filtered queries at about
  15-60 ms. `st.cache_data` caches each query result, so repeating a filter is instant. The map always draws
  every flagged shop and samples the rest (adjustable) to stay responsive. If MySQL is unavailable, the app
  runs the same SQL on a local SQLite copy.

## 7. Policy insight: ONORC across urban and rural zones
* **Urban:** 11% of Portability-Active Towns moved into the Urban Hub persona in one year, and 45% of urban
  volatile shops settled into the hub pattern. ONORC is concentrating demand in metro hub shops.
* **Rural:** shops are stabilising. Only 44% of volatile rural shops stayed volatile, and the rest moved to
  Steady or Portability-Active. Rural portability is rising slowly but steadily (+2.5 points in two years).

## 8. Recommendations
1. **Allocate urban stock by last month's transactions, not by attached cards.** In hub shops two-thirds of
   demand comes from cards registered elsewhere.
2. **Restock portability hubs fortnightly.** They're concentrated in 109 circle offices, led by Uppal-2,
   Saroornagar-3, Balanagar-1 and Charminar-3.
3. **Prioritise field audits and e-KYC re-verification for the Volatile Under-Utilized persona**, where 1 in 5
   shop-years is flagged.
4. **Use persona-specific thresholds** in monitoring. A utilization of 1.0 is normal for a hub but unusual for an
   Antyodaya-heavy village shop.
5. **Protect supply to Antyodaya-Heavy shops.** They serve the poorest households and distribute the most rice
   per transaction.

## 9. Limitations and next steps
* The urban label is district-level. Ward or mandal-level urban classification would sharpen the purity analysis.
* The flags are statistical. Inspection outcomes are needed as ground truth to measure precision, and could then
  train a supervised model.
* Adding allocation (stock received) data would allow a direct stock-reconciliation check: allocated vs
  distributed.
