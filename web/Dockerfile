FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# SPA routing: unknown paths serve index.html; /meetings proxies to the API.
# client_max_body_size 0: unlimited upload size -- this is self-hosted, the
# operator controls what gets uploaded, and the default 1MB rejects every
# real recording with 413 before it reaches the API.
# proxy_request_buffering off: stream the upload straight to the API instead
# of nginx spooling the whole file first. proxy_buffering (response
# direction, for SSE) stays off separately -- do not conflate the two.
RUN printf 'server {\n\
  listen 80;\n\
  client_max_body_size 0;\n\
  location /meetings {\n\
    proxy_pass http://api:8000;\n\
    proxy_request_buffering off;\n\
    proxy_buffering off;\n\
  }\n\
  location / { root /usr/share/nginx/html; try_files $uri /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
