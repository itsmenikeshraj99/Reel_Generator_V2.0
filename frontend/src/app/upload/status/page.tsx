import { Suspense } from "react";

import Loading from "@/app/loading";
import StatusClient from "./StatusClient";

export const dynamic = "force-dynamic";

export default function StatusPage() {
  return (
    <Suspense fallback={<Loading />}>
      <StatusClient />
    </Suspense>
  );
}
