# Build the React UI, serve the static bundle with nginx.
# Build from the repo root: docker build -f src/ui/Dockerfile -t agent-ui:latest .
FROM node:20-slim AS build
WORKDIR /app
COPY src/ui/package.json src/ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/ui/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY src/ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
