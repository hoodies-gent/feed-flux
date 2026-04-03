FROM node:22-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# next.js bakes rewrites at build time, so we must expose the docker network url during build
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=${BACKEND_URL}

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
