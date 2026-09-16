import { createClient } from "@supabase/supabase-js";

// Browser-side Supabase client, used only for authentication (sign-in for
// invited users). There is no signup call anywhere in this file or
// elsewhere in the app -- see CLAUDE.md Section 3 ("No public signup") and
// Section 10 ("Build a public /signup route ... never").
// Falls back to a syntactically-valid placeholder so a build without a real
// .env (e.g. CI, or before SETUP.md Step 1 is done) doesn't crash at module
// load -- createClient() validates its URL argument eagerly. Any real
// request against the placeholder host will simply fail at runtime, which
// is correct: there is no working backend to reach until it's configured.
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "https://placeholder.supabase.co";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "placeholder-anon-key";

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
