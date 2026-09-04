import { Suspense } from "react";

import Loading from "@/app/loading";
import GalleryClient from "./GalleryClient";

export const dynamic = "force-dynamic";

export default function GalleryPage() {
  return (
    <Suspense fallback={<Loading />}>
      <GalleryClient />
    </Suspense>
  );
}
