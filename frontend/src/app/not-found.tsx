import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-screen bg-dark text-white flex flex-col items-center justify-center p-6">
      <h1 className="text-6xl font-extrabold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
        404
      </h1>
      <p className="text-gray-400 mt-4">Page not found</p>
      <Link
        href="/"
        className="mt-8 bg-white text-black px-6 py-3 rounded-full font-bold"
      >
        Go home
      </Link>
    </div>
  );
}
