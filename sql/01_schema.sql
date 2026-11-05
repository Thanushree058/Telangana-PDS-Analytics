-- =====================================================================
-- Telangana PDS Analytics - MySQL schema
-- Run this once in MySQL Workbench (File > Open SQL Script > Execute),
-- or let src/load_to_mysql.py run it for you.
--
-- Design: a small star schema.
--   dim_shop             one row per Fair Price Shop (who / where)
--   fact_shop_month      one row per shop per month (what happened)
--   shop_year_clusters   model output: features + persona + anomaly flags
--   cluster_profiles     persona summary table
-- =====================================================================

CREATE DATABASE IF NOT EXISTS telangana_pds;
USE telangana_pds;

DROP TABLE IF EXISTS shop_year_clusters;
DROP TABLE IF EXISTS fact_shop_month;
DROP TABLE IF EXISTS cluster_profiles;
DROP TABLE IF EXISTS dim_shop;

CREATE TABLE dim_shop (
    distCode     INT          NOT NULL,
    shopNo       BIGINT       NOT NULL,
    distName     VARCHAR(60),
    officeCode   INT,
    officeName   VARCHAR(80),
    area_type    ENUM('Urban','Rural'),
    address      VARCHAR(255),
    latitude     DECIMAL(9,6),
    longitude    DECIMAL(9,6),
    fpsStatus    VARCHAR(30),
    fpsType      VARCHAR(40),
    PRIMARY KEY (distCode, shopNo),
    INDEX idx_dist (distName)
);

CREATE TABLE fact_shop_month (
    distCode          INT     NOT NULL,
    shopNo            BIGINT  NOT NULL,
    year              SMALLINT NOT NULL,
    month             TINYINT  NOT NULL,
    noOfRcs           INT,           -- cards that transacted
    noOfTrans         INT,           -- transactions
    totalRcs          INT,           -- cards attached to shop (entitlement)
    totalUnits        INT,           -- family members covered
    rcNfsaAay         INT,           -- Antyodaya (poorest) cards
    otherShopTransCnt INT,           -- portability transactions (ONORC)
    rice              DECIMAL(12,2), -- kg, all schemes
    wheat             DECIMAL(12,2),
    sugar             DECIMAL(12,2),
    PRIMARY KEY (distCode, shopNo, year, month),
    CONSTRAINT fk_fact_shop FOREIGN KEY (distCode, shopNo) REFERENCES dim_shop (distCode, shopNo)
);

CREATE TABLE shop_year_clusters (
    distCode            INT     NOT NULL,
    shopNo              BIGINT  NOT NULL,
    year                SMALLINT NOT NULL,
    total_trans         BIGINT,
    portability_trans   BIGINT,
    avg_cards           DOUBLE,
    avg_monthly_trans   DOUBLE,
    utilization_ratio   DOUBLE,
    portability_share   DOUBLE,
    rice_per_trans      DOUBLE,
    wheat_share         DOUBLE,
    aay_share           DOUBLE,
    trans_cv            DOUBLE,
    active_month_share  DOUBLE,
    pc1 DOUBLE, pc2 DOUBLE, pc3 DOUBLE,
    cluster             TINYINT,
    persona             VARCHAR(40),
    dbscan_noise        BOOLEAN,
    z_utilization_ratio DOUBLE,
    z_rice_per_trans    DOUBLE,
    peer_z_max          DOUBLE,
    peer_outlier        BOOLEAN,
    anomaly_flag        BOOLEAN,
    portability_hub     BOOLEAN,
    PRIMARY KEY (distCode, shopNo, year),
    -- The dashboard always filters by year first, then persona or the
    -- anomaly flag. A composite index in that order lets MySQL jump
    -- straight to the matching rows instead of scanning the table.
    INDEX idx_year_persona (year, persona),
    INDEX idx_year_flag (year, anomaly_flag),
    CONSTRAINT fk_cluster_shop FOREIGN KEY (distCode, shopNo) REFERENCES dim_shop (distCode, shopNo)
);

CREATE TABLE cluster_profiles (
    persona            VARCHAR(40) PRIMARY KEY,
    shops              INT,
    urban_pct          DOUBLE,
    avg_cards          DOUBLE,
    avg_monthly_trans  DOUBLE,
    utilization_ratio  DOUBLE,
    portability_share  DOUBLE,
    rice_per_trans     DOUBLE,
    wheat_share        DOUBLE,
    aay_share          DOUBLE,
    trans_cv           DOUBLE,
    dbscan_noise_pct   DOUBLE,
    anomaly_pct        DOUBLE
);
