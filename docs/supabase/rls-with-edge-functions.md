# Supabase RLS with Edge Functions (Custom Auth)

## Problem
Supabase sends security warnings when:
1. RLS policies exist but RLS is not enabled
2. Public tables have RLS disabled

These warnings appear even when using custom authentication (not Supabase Auth).

## Architecture Context

Our dashboard uses:
- **Custom auth** (not Supabase Auth) - tokens stored in `user_sessions` table
- **Edge Functions** with `service_role` key - bypasses RLS
- **Frontend** uses `anon` key to call edge functions only
- Edge functions deployed with `--no-verify-jwt` (custom tokens aren't Supabase JWTs)

```
Frontend ──[anon key + custom token]──→ Edge Functions ──[service_role]──→ Supabase DB
```

## Solution

### Enable RLS with Service Role Only Policy

Since edge functions use `service_role` (which bypasses RLS), enable RLS and block direct `anon` access:

```sql
-- Enable RLS on all tables
ALTER TABLE public.extracted_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.question_clusters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.question_cluster_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tb_stat ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.qa_entries ENABLE ROW LEVEL SECURITY;

-- Policy: ONLY service_role can access
CREATE POLICY "Service role only" ON public.{table_name}
  FOR ALL USING (auth.role() = 'service_role');
```

## Key Insight

| Key Type | Purpose | RLS Behavior |
|----------|---------|--------------|
| `anon` | Client-side, public | Subject to RLS |
| `service_role` | Server-side, private | **Bypasses RLS entirely** |

Edge functions using `service_role` don't need RLS policies - they bypass all security.
The "Service role only" policy just blocks direct `anon` key access to tables.

## Why `--no-verify-jwt` is Required

Custom tokens (from `generateToken()`) are NOT Supabase JWTs. Supabase JWT verification fails because:
- Different signing key
- Different payload structure
- Not issued by Supabase Auth

Token verification happens manually in edge functions via `getCurrentUser(req)` which queries `user_sessions` table.

## Gotchas

1. **Don't enable RLS without policies** - breaks all access
2. **service_role key must stay server-side** - never expose to frontend
3. **WARN vs ERROR** - WARN level (like function search_path) can often be safely ignored

## Date Learned
2024-12-24

## Related
- VoilaFrontEnd: `/home/cooky/Projects/Clients Project/Rudy/VoilaFrontEnd`
- Edge functions: `supabase/functions/_shared/supabase.ts`
- Custom auth: `supabase/functions/_shared/auth.ts`
