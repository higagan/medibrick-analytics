-- Add repost_count column to leads table
-- Run this in your Supabase SQL editor (https://supabase.com/dashboard/project/_/editor)

ALTER TABLE leads ADD COLUMN IF NOT EXISTS repost_count INTEGER DEFAULT 1;

-- Update existing rows to have repost_count = 1
UPDATE leads SET repost_count = 1 WHERE repost_count IS NULL;
