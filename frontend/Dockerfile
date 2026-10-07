# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_BACKEND_URL=http://localhost:8000
ENV VITE_BACKEND_URL=$VITE_BACKEND_URL

ARG VITE_AUTHELIA_URL=
ENV VITE_AUTHELIA_URL=$VITE_AUTHELIA_URL

RUN npm run build

# The stable branch. 1.29 stopped receiving updates when 1.30 became stable,
# and the first trivy run found every one of its 126 findings fixed upstream
# (ai-trainer-ops#32).
FROM nginx:1.30-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]