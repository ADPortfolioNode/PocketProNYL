ARG NODE_VERSION=22.13.1
FROM node:${NODE_VERSION}-slim AS builder
WORKDIR /app

ARG REACT_APP_API_BASE=
ARG CACHE_BUSTER=1
ENV REACT_APP_API_BASE=$REACT_APP_API_BASE
ENV CI=true
ENV GENERATE_SOURCEMAP=false
ENV DISABLE_ESLINT_PLUGIN=true
ENV TSC_COMPILE_ON_ERROR=true
ENV NODE_OPTIONS=--max-old-space-size=1536

COPY package*.json ./
RUN npm ci --legacy-peer-deps || npm ci
COPY . .
RUN echo "cache_buster=$CACHE_BUSTER" \
    && npm run build \
    && test -f /app/build/index.html

FROM nginx:alpine
RUN rm -rf /usr/share/nginx/html/*
COPY --from=builder /app/build/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/nginx.conf

RUN mkdir -p /var/run/nginx /var/cache/nginx /var/log/nginx \
    && chown -R nginx:nginx /usr/share/nginx/html /etc/nginx/nginx.conf /var/cache/nginx /var/run/nginx /var/log/nginx \
    && chmod -R 755 /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=2 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1/ || exit 1

USER nginx
CMD ["nginx", "-g", "daemon off;"]
