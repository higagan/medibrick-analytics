-- Create analytics_checks table for monitoring data
-- Run this in your Supabase SQL editor

CREATE TABLE IF NOT EXISTS analytics_checks (
    id SERIAL PRIMARY KEY,
    status VARCHAR(50),
    content VARCHAR(50),
    security_headers VARCHAR(50),
    ssl_days INTEGER,
    ssl_status VARCHAR(50),
    deploy_status VARCHAR(50),
    response_time DECIMAL(10,6),
    dns_ip VARCHAR(100),
    checked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_analytics_checked_at ON analytics_checks(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_status ON analytics_checks(status);
