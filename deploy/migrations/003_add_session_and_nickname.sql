-- 单设备登录（互踢）所需字段 + 老用户昵称修补
-- 幂等：用 information_schema 判重，重复执行安全

-- 1. users.session_id：当前有效会话标识
SET @db = DATABASE();
SET @exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'users' AND COLUMN_NAME = 'session_id'
);
SET @sql = IF(@exists = 0,
  'ALTER TABLE users ADD COLUMN session_id VARCHAR(64) NULL COMMENT ''当前会话ID，登录刷新，用于单设备互踢'' AFTER avatar',
  'SELECT ''session_id already exists'' AS info'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 索引：加速按会话查找（非必须，便于排障）
SET @idx_exists = (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'users' AND INDEX_NAME = 'ix_users_session_id'
);
SET @sql2 = IF(@idx_exists = 0,
  'CREATE INDEX ix_users_session_id ON users(session_id)',
  'SELECT ''ix_users_session_id already exists'' AS info'
);
PREPARE stmt2 FROM @sql2; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

-- 2. 修补历史 NULL 昵称：改为「用户+手机后4位」，避免前端显示 null
UPDATE users
SET nickname = CONCAT('用户', RIGHT(phone, 4))
WHERE nickname IS NULL OR nickname = '';
