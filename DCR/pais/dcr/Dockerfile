FROM node:22-alpine
RUN apk add --no-cache git
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY server.mts p4_career_advancement_tobe.xml ./
EXPOSE 8090
CMD ["npx", "tsx", "server.mts"]
