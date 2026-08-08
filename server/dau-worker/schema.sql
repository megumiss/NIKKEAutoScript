-- NKAS DAU 统计表
-- 初始化：npx wrangler d1 execute nkas-dau --remote --file=schema.sql
CREATE TABLE IF NOT EXISTS reports (
  day TEXT NOT NULL,   -- 日期 YYYY-MM-DD（UTC+8）
  id  TEXT NOT NULL,   -- 匿名 ID
  v   TEXT NOT NULL,   -- 客户端版本
  os  TEXT NOT NULL,   -- 系统
  res TEXT NOT NULL,   -- 分辨率
  geo TEXT NOT NULL,   -- 地区（CF 边缘节点两位国家码）
  PRIMARY KEY (day, id)
);
