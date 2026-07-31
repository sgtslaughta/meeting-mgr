FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# SPA routing: unknown paths serve index.html; /meetings proxies to the API.
RUN printf 'server {\n\
  listen 80;\n\
  location /meetings { proxy_pass http://api:8000; proxy_buffering off; }\n\
  location / { root /usr/share/nginx/html; try_files $uri /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
