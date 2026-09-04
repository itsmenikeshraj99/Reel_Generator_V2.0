# syntax=docker/dockerfile:1.6
# =============================================================
# Frontend Dockerfile — Cloud Run (alternative to Vercel)
# Multi-stage: install deps + build in builder, then ship a slim
# runtime with only the standalone output.
# =============================================================

# ---------- builder ----------
FROM node:20-alpine AS builder

WORKDIR /app

# Copy manifests first for layer caching
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# Build args are baked into the JS bundle by Next.js at build time
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# Tell next.config.js to produce a standalone output (smaller Docker image)
ENV BUILD_STANDALONE=1

# Build the Next.js app
COPY . .
RUN npm run build

# ---------- runtime ----------
FROM node:20-alpine AS runtime

WORKDIR /app

# Copy only what we need to run
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public 2>/dev/null || true

# Run as non-root
RUN addgroup -g 65532 app && adduser -u 65532 -G app -D -s /sbin/nologin app \
 && chown -R app:app /app
USER 65532

ENV PORT=8080
ENV HOSTNAME=0.0.0.0
EXPOSE 8080

CMD ["node", "server.js"]
