-- 迁移 001：为 users 表新增账号密码登录与头像字段
-- 适用：线上 MySQL 8（SQLite 由 create_all 自动建表，无需此脚本）
-- 执行前请先对 users 表备份。
--
-- 若 app.config 中 MYSQL_TABLE_PREFIX 非空（例如 "sl_"），
-- 请将下方表名替换为带前缀的版本（如 sl_users）。

ALTER TABLE `users`
    ADD COLUMN `password_hash` VARCHAR(255) NULL COMMENT 'PBKDF2 密码哈希，空表示仅支持验证码登录',
    ADD COLUMN `avatar` VARCHAR(512) NULL COMMENT '头像 URL 或存储标识，空时前端用昵称首字默认头像';

-- 说明：
-- 1. password_hash 采用 server/app/core/security.py 的 hash_password（PBKDF2-HMAC-SHA256 + 随机盐，Base64 存储）。
-- 2. 存量老用户 password_hash 为 NULL，仍可走验证码登录；首次设置密码即写入。
-- 3. avatar 为空时，App 端用昵称首字符生成默认头像，不依赖云端图床。
