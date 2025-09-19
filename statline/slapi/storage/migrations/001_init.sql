-- 001_init.sql (patched)
-- No PRAGMAs here; enable foreign_keys per-connection in app code.

CREATE TABLE IF NOT EXISTS guild_config (
  guild_id            TEXT PRIMARY KEY,
  sheet_key           TEXT NOT NULL,
  sheet_tab           TEXT NOT NULL DEFAULT 'STATS',
  last_sync_ts        INTEGER NOT NULL DEFAULT 0,
  last_forced_update  INTEGER NOT NULL DEFAULT 0,
  rate_limit_day      TEXT DEFAULT NULL,
  created_ts          INTEGER NOT NULL,
  updated_ts          INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS teams (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id  TEXT NOT NULL
            REFERENCES guild_config(guild_id) ON DELETE CASCADE,
  team_name TEXT NOT NULL,
  wins      INTEGER NOT NULL DEFAULT 0 CHECK (wins >= 0),
  losses    INTEGER NOT NULL DEFAULT 0 CHECK (losses >= 0),
  UNIQUE (guild_id, team_name)
) STRICT;

CREATE TABLE IF NOT EXISTS players (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id     TEXT NOT NULL
               REFERENCES guild_config(guild_id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,  -- canonical name
  fuzzy_key    TEXT GENERATED ALWAYS AS (lower(trim(display_name))) STORED,
  team_name    TEXT,

  -- cached per-game stats (last synced)
  ppg REAL NOT NULL DEFAULT 0 CHECK (ppg >= 0),
  apg REAL NOT NULL DEFAULT 0 CHECK (apg >= 0),
  orpg REAL NOT NULL DEFAULT 0 CHECK (orpg >= 0),
  drpg REAL NOT NULL DEFAULT 0 CHECK (drpg >= 0),
  spg REAL NOT NULL DEFAULT 0 CHECK (spg >= 0),
  bpg REAL NOT NULL DEFAULT 0 CHECK (bpg >= 0),
  fgm REAL NOT NULL DEFAULT 0 CHECK (fgm >= 0),
  fga REAL NOT NULL DEFAULT 0 CHECK (fga >= 0),
  tov REAL NOT NULL DEFAULT 0 CHECK (tov >= 0),

  UNIQUE (guild_id, fuzzy_key)
) STRICT;

-- NOTE: Max-stats storage has been fully removed (no JSON, no KV). If you need
-- adapter caps or maxima, manage them externally (e.g., CSV) and feed into scoring.
