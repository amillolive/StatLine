-- No PRAGMA here; set foreign_keys in app code.

-- Lookups/lists by display name within a guild
CREATE INDEX IF NOT EXISTS idx_players_guild_display
  ON players(guild_id, display_name);

-- Team roster listing & sorting (keep if used; high value for guild+team queries)
CREATE INDEX IF NOT EXISTS idx_players_guild_team_display
  ON players(guild_id, team_name, display_name);

-- Only if you frequently filter by rate_limit_day (use NULL-friendly partial)
CREATE INDEX IF NOT EXISTS idx_gconfig_rate_day_present
  ON guild_config(rate_limit_day)
  WHERE rate_limit_day IS NOT NULL;
