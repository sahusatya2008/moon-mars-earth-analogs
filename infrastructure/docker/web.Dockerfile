# syntax=docker/dockerfile:1
# Multi-stage Next.js standalone image.
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
# The npm cache mount keeps downloads across interrupted/retried builds.
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./
# Empty means same-origin browser requests through the Next.js API proxy.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S terrax && adduser -S terrax -G terrax

# standalone bundles only the server and traced dependencies.
COPY --from=builder --chown=terrax:terrax /app/.next/standalone ./
COPY --from=builder --chown=terrax:terrax /app/.next/static ./.next/static

USER terrax
EXPOSE 3000
CMD ["node", "server.js"]
