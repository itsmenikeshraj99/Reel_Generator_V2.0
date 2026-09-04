import { Suspense } from "react";

import DashboardClient from "./DashboardClient";
import { Skeleton } from "@/components/Skeleton";

export const dynamic = "force-dynamic";

/**
 * Dashboard — list of the caller's videos with their reel counts and
 * latest job stage. Server component shells out to the client component
 * for auth-gated data fetching (matches the rest of the app's pattern).
 */
export default function DashboardPage() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <DashboardClient />
    </Suspense>
  );
}

function DashboardSkeleton() {
  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
      <div className="flex items-center justify-between mb-8">
        <Skeleton width={180} height={32} />
        <Skeleton width={140} height={40} className="rounded-full" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} height={220} className="rounded-2xl" />
        ))}
      </div>
    </div>
  );
}
