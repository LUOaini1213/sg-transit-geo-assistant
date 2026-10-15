# Web image: build the React app, then serve it with nginx, which also forwards /api to the API container.
# Base images are pinned by digest (tag kept for reading); refresh both when the security job reports fixable CVEs.
FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# nginx 1.30 (stable line; 1.29 no longer gets image rebuilds) in NGINX's unprivileged variant: runs as uid 101, listens on 8080
FROM nginxinc/nginx-unprivileged:1.30-alpine@sha256:782bb89768cc87b7f95c18b95baa1af250e5d451f26794f59363540161147c01
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
