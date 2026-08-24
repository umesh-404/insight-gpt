# InsightGPT web image — Next.js frontend (build + `next start`).
#
# Build context is the REPO ROOT (see docker/compose.yml); only web/ is copied.
#
#   docker build -f docker/web.Dockerfile -t insightgpt-web .
#
# NEXT_PUBLIC_* values are inlined at BUILD time by Next.js, so they arrive as
# build args (and are also set at runtime for `next start`).

# --- deps: install node_modules from the lockfile ----------------------------
FROM node:20-bookworm-slim AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci

# --- build: compile the production app ---------------------------------------
FROM node:20-bookworm-slim AS build
WORKDIR /app

# Public config must be present before `next build` bakes it into the bundle.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_USE_MOCK=false
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_USE_MOCK=$NEXT_PUBLIC_USE_MOCK \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY web/ ./
RUN npm run build

# --- runtime: serve with `next start` ----------------------------------------
FROM node:20-bookworm-slim AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# Ship the built app plus its production dependencies.
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/next.config.js ./next.config.js

USER node
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD node -e "require('http').get('http://localhost:3000',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"

CMD ["npm", "run", "start", "--", "-p", "3000"]
