-- One-off prod data fix — applied 2026-07-18 by Jürgen Hahn.
--
-- Context: the athlete asked the coach to make Saturday 2026-07-18 a "3-4h
-- endurance" ride. Due to the intra-day field-drift bug (see #422 / #424), the
-- coach chat updated only the description prose while the numeric duration
-- drifted. A later automated write then canonicalized the day toward a stale
-- 45/60-minute window (durationMinutes = 52), leaving Saturday as a ~52-minute
-- ride that contradicted both the athlete's request and the "3 hours" prose.
--
-- This restores the day to the intended 3-4h endurance window (180-240 min,
-- midpoint 210) and aligns the description. Scoped to a single athlete's own
-- account; keeps source="user" (the day stays pinned).
--
-- The structural fix that prevents recurrence is the canonical PlanDay persist
-- gate (#424 / PR #425). This script is the audit record of the manual repair.
--
-- Run inside the postgres container:
--   docker exec -i ai-trainer-postgres-1 sh -c \
--     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < 2026-07-18_fix_saturday_duration.sql
--
-- The WHERE guard asserts array index 13 is really 2026-07-18, so a re-run
-- against a reordered plan is a no-op (0 rows) rather than a wrong write.

UPDATE training_plans tp
SET plan = (
  jsonb_set(jsonb_set(jsonb_set(jsonb_set(
    tp.plan::jsonb,
    '{13,durationMinutes}',    '210'::jsonb, true),
    '{13,durationMinMinutes}', '180'::jsonb, true),
    '{13,durationMaxMinutes}', '240'::jsonb, true),
    '{13,description}',
    to_jsonb('Ride for 3 to 4 hours keeping your power steady in your Zone 2 range of 208-240W. Focus on smooth, consistent pedaling and keep the surges to a minimum.'::text),
    true)
)::json
WHERE tp.user_id = (SELECT id FROM users WHERE email = 'mail.jhahn@gmail.com')
  AND tp.plan::jsonb -> 13 ->> 'date' = '2026-07-18';

-- Verify
SELECT elem.value->>'date'              AS date,
       elem.value->>'durationMinutes'    AS scalar,
       elem.value->>'durationMinMinutes' AS lo,
       elem.value->>'durationMaxMinutes' AS hi,
       elem.value->>'source'             AS source
FROM training_plans tp,
     LATERAL jsonb_array_elements(tp.plan::jsonb) elem
WHERE tp.user_id = (SELECT id FROM users WHERE email = 'mail.jhahn@gmail.com')
  AND elem.value->>'date' = '2026-07-18';
