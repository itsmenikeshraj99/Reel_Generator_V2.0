/**
 * Tiny class-name joiner. We don't need `clsx` or `tailwind-merge` for the
 * handful of simple class strings this codebase uses — saves a dep and a
 * bundle.
 */
export function cn(...args: Array<string | false | null | undefined>): string {
  return args.filter(Boolean).join(" ");
}
