FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci && npm run build   # 建置
ENV NODE_ENV=production PORT=$PORT
CMD ["node", "dist/server.js"]
