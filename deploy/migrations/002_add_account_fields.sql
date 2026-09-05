-- ============================================================
-- 002_add_account_fields.sql
--
-- 为 users 表新增 password_hash / avatar 两个字段（账号密码登录体系）。
--
-- 幂等性：本脚本可重复执行，列已存在则跳过 ALTER，不会报错。
-- 适用于两种场景：
--   1) 全新库：001_init.sql 建表（无新字段）后，本脚本补齐；
--   2) 已在运行的库（上一版部署）：001_init.sql 的 CREATE TABLE IF NOT EXISTS
--      不会改动已有表，本脚本负责给旧表追加字段。
--
-- 用法：
--   mysql --defaults-extra-file=... survolocking < 002_add_account_fields.sql
-- ============================================================

SET NAMES utf8mb4;

-- ---------------------------------------------------------- password_hash
SELECT COUNT(*) INTO @col_exists
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME   = 'users'
  AND COLUMN_NAME  = 'password_hash';

SET @sql = IF(
  @col_exists = 0,
  "ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NULL COMMENT 'PBKDF2 密码哈希，为空表示仅支持验证码登录'",
  "SELECT 1"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------- avatar
SELECT COUNT(*) INTO @col_exists
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME   = 'users'
  AND COLUMN_NAME  = 'avatar';

SET @sql = IF(
  @col_exists = 0,
  "ALTER TABLE users ADD COLUMN avatar VARCHAR(512) NULL COMMENT '头像 URL 或存储标识，为空时前端用昵称首字默认头像'",
  "SELECT 1"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SELECT '002_add_account_fields: OK' AS result;
