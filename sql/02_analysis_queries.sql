-- =====================================================================
-- Telangana PDS Analytics - business questions in SQL
-- Open in MySQL Workbench and run one query at a time (Ctrl+Enter).
-- =====================================================================
USE telangana_pds;

-- 1. Data check: rows per year
SELECT year, COUNT(*) AS shop_months, COUNT(DISTINCT shopNo) AS shops
FROM fact_shop_month GROUP BY year ORDER BY year;

-- 2. Policy impact (One Nation One Ration Card):
--    portability share by year, Urban vs Rural
SELECT f.year, s.area_type,
       ROUND(SUM(f.otherShopTransCnt) / SUM(f.noOfTrans) * 100, 2) AS portability_pct
FROM fact_shop_month f
JOIN dim_shop s USING (distCode, shopNo)
GROUP BY f.year, s.area_type
ORDER BY s.area_type, f.year;

-- 3. Entitlement vs actual: utilization by district (latest year)
SELECT s.distName,
       ROUND(SUM(f.noOfTrans) / SUM(f.totalRcs), 3) AS utilization_ratio,
       SUM(f.totalRcs)  AS card_months,
       SUM(f.noOfTrans) AS transactions
FROM fact_shop_month f
JOIN dim_shop s USING (distCode, shopNo)
WHERE f.year = 2024
GROUP BY s.distName
ORDER BY utilization_ratio DESC;

-- 4. Seasonality: total transactions and rice by calendar month
SELECT month, SUM(noOfTrans) AS transactions, ROUND(SUM(rice) / 1000, 1) AS rice_tonnes
FROM fact_shop_month
WHERE year IN (2023, 2024)
GROUP BY month ORDER BY month;

-- 5. Persona sizes and how urban each is (cluster purity)
SELECT * FROM cluster_profiles ORDER BY shops DESC;

-- 6. Fraud screening: top 25 shops furthest from their peer cluster
SELECT c.year, s.distName, s.officeName, c.shopNo, c.persona,
       ROUND(c.utilization_ratio, 2) AS utilization,
       ROUND(c.rice_per_trans, 1)    AS rice_kg_per_trans,
       ROUND(c.z_utilization_ratio, 1) AS z_util,
       ROUND(c.z_rice_per_trans, 1)    AS z_rice,
       c.dbscan_noise
FROM shop_year_clusters c
JOIN dim_shop s USING (distCode, shopNo)
WHERE c.anomaly_flag = 1
ORDER BY GREATEST(ABS(c.z_utilization_ratio), ABS(c.z_rice_per_trans)) DESC
LIMIT 25;

-- 7. Districts with the most flagged shops (hotspots)
SELECT s.distName, c.year,
       SUM(c.anomaly_flag) AS flagged,
       COUNT(*)            AS shops,
       ROUND(SUM(c.anomaly_flag) / COUNT(*) * 100, 2) AS flagged_pct
FROM shop_year_clusters c
JOIN dim_shop s USING (distCode, shopNo)
GROUP BY s.distName, c.year
ORDER BY flagged_pct DESC
LIMIT 15;

-- 8. Logistics: portability hubs that need more frequent restocking
SELECT s.distName, s.officeName, c.shopNo, c.year,
       ROUND(c.avg_monthly_trans) AS avg_monthly_trans,
       ROUND(c.portability_share * 100, 1) AS portability_pct
FROM shop_year_clusters c
JOIN dim_shop s USING (distCode, shopNo)
WHERE c.portability_hub = 1 AND c.year = 2024
ORDER BY c.avg_monthly_trans DESC
LIMIT 25;

-- 9. Persona migration 2023 -> 2024 (did shops change behaviour?)
SELECT a.persona AS persona_2023, b.persona AS persona_2024, COUNT(*) AS shops
FROM shop_year_clusters a
JOIN shop_year_clusters b
  ON a.distCode = b.distCode AND a.shopNo = b.shopNo AND a.year = 2023 AND b.year = 2024
GROUP BY a.persona, b.persona
ORDER BY persona_2023, shops DESC;
