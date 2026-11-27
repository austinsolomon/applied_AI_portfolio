# ILLUSTRATIVE - not run. Rung 4: the container the worker lives in.
#
# Never built or pushed by this repo. The SDK starts a `claude` process with a shell, a working
# directory and its own transcripts on disk, so its home is a container, not a serverless function.
# Budget about 1 GiB of memory, 5 GiB of disk and 1 CPU per agent running at once.
FROM node:22-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
CMD ["node", "dist/worker/server.js"]
