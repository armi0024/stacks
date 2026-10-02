-- Seed per DECISIONS.md (pre-build decisions, resolved):
-- domains: archive, paradise, starcade, retrolodge, personal
-- owner -> all domains restricted, all verbs
-- shop session -> archive internal, read/search only
-- no agent tokens until phase 6

INSERT OR IGNORE INTO settings(key, value) VALUES
  ('domains', '["archive","paradise","starcade","retrolodge","personal"]'),
  ('capacity_threshold', '0.8'),
  ('principal_precedence', '["owner"]');

INSERT OR IGNORE INTO principals(id, name, kind, grants, verbs, active, created_at) VALUES
  ('prin_owner', 'owner', 'owner',
   '{"archive":"restricted","paradise":"restricted","starcade":"restricted","retrolodge":"restricted","personal":"restricted"}',
   '["read","search","submit","ext-write","restricted-provenance-write","authority","admin"]',
   1, datetime('now')),
  ('prin_shop', 'shop-session', 'session',
   '{"archive":"internal"}',
   '["read","search"]',
   1, datetime('now'));
