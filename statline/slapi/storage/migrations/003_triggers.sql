-- 003_triggers.sql (patched)
-- No PRAGMAs here; enable foreign_keys per-connection in app code.

-- ─────────────── guild_config timestamps ───────────────

CREATE TRIGGER IF NOT EXISTS trg_gconfig_insert_ts
AFTER INSERT ON guild_config
FOR EACH ROW
WHEN (NEW.created_ts IS NULL OR NEW.created_ts = 0)
   OR (NEW.updated_ts IS NULL OR NEW.updated_ts = 0)
BEGIN
  UPDATE guild_config
     SET created_ts = COALESCE(NULLIF(NEW.created_ts, 0), CAST(strftime('%s','now') AS INTEGER)),
         updated_ts = COALESCE(NULLIF(NEW.updated_ts, 0), created_ts)
   WHERE guild_id = NEW.guild_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_gconfig_touch
AFTER UPDATE ON guild_config
FOR EACH ROW
WHEN NEW.updated_ts = OLD.updated_ts
BEGIN
  UPDATE guild_config
     SET updated_ts = CAST(strftime('%s','now') AS INTEGER)
   WHERE guild_id = NEW.guild_id;
END;

-- No max-stats triggers (feature deprecated).
-- Players/teams currently have no timestamp columns; add and mirror pattern if needed later.
