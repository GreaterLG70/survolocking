-- ============================================================
-- Survolocking MySQL 初始化脚本
--
-- 用法一（推荐，独立库）：
--   CREATE DATABASE survolocking CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
--   USE survolocking;
--   SOURCE 001_init.sql;
--
-- 用法二（无建库权限，表前缀模式）：
--   在 .env 中设置 MYSQL_TABLE_PREFIX=sl_，然后手工为本脚本中的
--   每个表名加上该前缀（应用端会自动加，本脚本需同步调整）。
--
-- 说明：应用启动时会自动执行 create_all 建表，本脚本主要用于
--       显式控制表结构、字符集与分区，生产环境建议以本脚本为准。
-- ============================================================

SET NAMES utf8mb4;

-- ------------------------------------------------------------ 用户
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT       NOT NULL AUTO_INCREMENT,
    phone       VARCHAR(20)  NOT NULL,
    nickname    VARCHAR(50)  DEFAULT NULL,
    push_token  VARCHAR(512) DEFAULT NULL,
    is_active   TINYINT(1)   NOT NULL DEFAULT 1,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_phone (phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ 家庭组
CREATE TABLE IF NOT EXISTS families (
    id          BIGINT      NOT NULL AUTO_INCREMENT,
    name        VARCHAR(50) NOT NULL,
    creator_id  BIGINT      NOT NULL,
    is_active   TINYINT(1)  NOT NULL DEFAULT 1,
    created_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_families_creator (creator_id),
    CONSTRAINT fk_families_creator FOREIGN KEY (creator_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS family_members (
    family_id  BIGINT     NOT NULL,
    user_id    BIGINT     NOT NULL,
    joined_at  DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active  TINYINT(1) NOT NULL DEFAULT 1,
    PRIMARY KEY (family_id, user_id),
    KEY idx_fm_user (user_id),
    CONSTRAINT fk_fm_family FOREIGN KEY (family_id) REFERENCES families (id),
    CONSTRAINT fk_fm_user   FOREIGN KEY (user_id)   REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS family_invitations (
    id            BIGINT      NOT NULL AUTO_INCREMENT,
    inviter_id    BIGINT      NOT NULL,
    family_id     BIGINT      NOT NULL,
    invitee_phone VARCHAR(20) NOT NULL,
    status        SMALLINT    NOT NULL DEFAULT 0 COMMENT '0待处理 1已接受 2已拒绝 3已过期',
    expire_at     DATETIME    NOT NULL,
    created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_invitee_status (invitee_phone, status),
    CONSTRAINT fk_fi_family FOREIGN KEY (family_id) REFERENCES families (id),
    CONSTRAINT fk_fi_inviter FOREIGN KEY (inviter_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ 拦截日志（按月分区）
--
-- MySQL 分区约束：分区键必须出现在所有唯一键（含主键）中，
-- 因此主键设计为 (id, call_time)，而非单独的 id。
-- 分区由应用端 database.ensure_month_partitions() 每日自动维护，
-- 此处创建初始分区以便直接使用。
CREATE TABLE IF NOT EXISTS intercept_logs (
    id              BIGINT      NOT NULL AUTO_INCREMENT,
    phone_hash      CHAR(64)    NOT NULL COMMENT '号码哈希，原始号码永不上云',
    family_id       BIGINT      DEFAULT NULL,
    user_id         BIGINT      NOT NULL,
    call_time       DATETIME    NOT NULL COMMENT '已截断到小时级',
    ring_duration   INT         DEFAULT NULL COMMENT '振铃时长（秒）',
    action          SMALLINT    NOT NULL COMMENT '1拦截 2放行 3用户拒接',
    decision_source SMALLINT    NOT NULL COMMENT '1本地黑名单 2起零 3微行为 4用户标记',
    is_false_positive TINYINT(1) NOT NULL DEFAULT 0,
    user_mark       SMALLINT    DEFAULT NULL COMMENT '1骚扰 2客户 3不确定',
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, call_time),
    KEY idx_log_family_time (family_id, call_time),
    KEY idx_log_hash (phone_hash),
    KEY idx_log_user (user_id)
    -- 注意：本表按月分区，MySQL 禁止分区表带外键，family_id/user_id 仅作逻辑关联，
    -- 引用完整性由应用层保证。
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
PARTITION BY RANGE COLUMNS(call_time) (
    PARTITION p_init VALUES LESS THAN (MAXVALUE)
);

-- ------------------------------------------------------------ 规则包
CREATE TABLE IF NOT EXISTS rule_packages (
    id          BIGINT        NOT NULL AUTO_INCREMENT,
    family_id   BIGINT        DEFAULT NULL,
    rule_type   SMALLINT      NOT NULL COMMENT '1黑名单 2号段 3行为规则 4白名单豁免',
    pattern     TEXT          NOT NULL,
    confidence  DECIMAL(3,2)  DEFAULT NULL,
    source      SMALLINT      NOT NULL COMMENT '1用户标记 2DeepSeek 3家庭聚合',
    status      SMALLINT      NOT NULL DEFAULT 0 COMMENT '0生效 1待审核 2已废弃',
    version     BIGINT        NOT NULL DEFAULT 0,
    expires_at  DATETIME      DEFAULT NULL,
    is_active   TINYINT(1)    NOT NULL DEFAULT 1,
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_rule_family_version (family_id, version),
    KEY idx_rule_active (family_id, is_active, status),
    CONSTRAINT fk_rule_family FOREIGN KEY (family_id) REFERENCES families (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ 短信验证码
CREATE TABLE IF NOT EXISTS sms_codes (
    id         BIGINT      NOT NULL AUTO_INCREMENT,
    phone      VARCHAR(20) NOT NULL,
    code       VARCHAR(10) NOT NULL,
    expire_at  DATETIME    NOT NULL,
    used       TINYINT(1)  NOT NULL DEFAULT 0,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_sms_phone (phone, used)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ 通知
CREATE TABLE IF NOT EXISTS notifications (
    id          BIGINT       NOT NULL AUTO_INCREMENT,
    user_id     BIGINT       NOT NULL,
    family_id   BIGINT       DEFAULT NULL,
    notify_type VARCHAR(32)  NOT NULL,
    title       VARCHAR(128) NOT NULL,
    content     VARCHAR(512) DEFAULT NULL,
    payload     TEXT         DEFAULT NULL,
    is_read     TINYINT(1)   NOT NULL DEFAULT 0,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_notify_user (user_id, is_read),
    CONSTRAINT fk_notify_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ 分析任务审计
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id                BIGINT   NOT NULL AUTO_INCREMENT,
    family_id         BIGINT   NOT NULL,
    log_count         INT      NOT NULL DEFAULT 0,
    rules_generated   INT      NOT NULL DEFAULT 0,
    prompt_tokens     INT      DEFAULT NULL,
    completion_tokens INT      DEFAULT NULL,
    status            VARCHAR(16) NOT NULL DEFAULT 'success',
    error_message     TEXT     DEFAULT NULL,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_job_family_created (family_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
