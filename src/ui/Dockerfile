# Build the React UI, serve the static bundle with nginx.
# Build from the repo root: docker build -f src/ui/Dockerfile -t agent-ui:latest .
# Bases are minor-pinned (node 20-slim; nginx-unprivileged 1.28-alpine).
# Digest pinning is deferred: digests are per-architecture and could not
# be resolved without docker here.
# Bases come from ECR Public, not Docker Hub (Docker Hub 429s on
# rate-limited networks).
#
# Build args — the OIDC/API values are baked into the bundle at build
# time (Vite reads them from the environment during `npm run build`), so
# pass them here, e.g.:
#   docker build --build-arg VITE_OIDC_ISSUER=... \
#                --build-arg VITE_OIDC_CLIENT_ID=... \
#                -f src/ui/Dockerfile -t agent-ui:latest .
# All default to empty (same as before: an unconfigured bundle shows the
# "Sign-in isn't configured" screen). deploy/local/run.sh passes them.
ARG VITE_OIDC_ISSUER=""
ARG VITE_OIDC_CLIENT_ID=""
ARG VITE_OIDC_AUDIENCE=""
ARG VITE_API_URL=""
FROM public.ecr.aws/docker/library/node:20-slim AS build
WORKDIR /app
COPY src/ui/package.json src/ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/ui/ ./
ENV VITE_OIDC_ISSUER=$VITE_OIDC_ISSUER \
    VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID \
    VITE_OIDC_AUDIENCE=$VITE_OIDC_AUDIENCE \
    VITE_API_URL=$VITE_API_URL
RUN npm run build

# nginx-unprivileged (m-27): runs as the nginx user (UID 101) and
# listens on 8080 — the stock nginx:alpine image runs as root on port
# 80, and merely adding USER nginx to it breaks on its root-owned
# cache/run paths. Keep nginx.conf's listen and the k8s Service
# targetPort on 8080 to match.
FROM public.ecr.aws/nginx/nginx-unprivileged:1.28-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY src/ui/nginx.conf /etc/nginx/conf.d/default.conf
USER nginx
EXPOSE 8080
