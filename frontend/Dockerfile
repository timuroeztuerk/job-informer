FROM node:20-alpine AS build
WORKDIR /app

COPY --chown=node:node frontend/package*.json ./
RUN npm ci

COPY --chown=node:node frontend .

USER node

# Allow overriding API base/token at build time
ARG VITE_API_BASE=http://localhost:8000
ARG VITE_API_TOKEN=
ENV VITE_API_BASE=${VITE_API_BASE}
ENV VITE_API_TOKEN=${VITE_API_TOKEN}

RUN npm run build

FROM nginx:alpine
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
