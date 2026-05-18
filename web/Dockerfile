# Two-stage Astro build. Stage 1 installs deps + compiles to a
# self-contained Node SSR bundle. Stage 2 ships only the runtime
# bits + production node_modules.

FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY astro.config.mjs tailwind.config.mjs postcss.config.* tsconfig.json ./
COPY public ./public
COPY src ./src
RUN npm run build

# Drop dev deps to shrink the runtime image.
RUN npm prune --omit=dev

FROM node:22-alpine AS runtime
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=4321
WORKDIR /app

# Node node_modules + the built dist/ folder are everything the
# adapter needs.
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./package.json

EXPOSE 4321
USER node
CMD ["node", "./dist/server/entry.mjs"]
