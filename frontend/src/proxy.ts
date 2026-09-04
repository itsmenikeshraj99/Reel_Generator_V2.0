// Authentication is enforced on the client (page-level useEffect) and on the
// server (Supabase RLS + backend Depends(get_current_user)). Edge middleware
// was too unreliable across the auth-helpers/SSR migrations, so we let the
// pages themselves gate access and let the API layer reject unauthorized calls.

import { NextResponse, type NextRequest } from "next/server";

export async function proxy(_req: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: ["/upload/:path*"],
};
