# Feed Bot

Discord bot with MongoDB-backed routing and message mapping.

## Configuration

Create `.env` from `.env.example` and fill in the required values.

Main variables:

- `DISCORD_TOKEN`
- `ALLOWED_GUILD_IDS`
- `MONGO_URI`
- `MONGO_DB`
- `MONGO_MESSAGE_MAPPING_COLLECTION`
- `MONGO_GUILD_ROUTES_COLLECTION`
- `MONGO_GUILD_PERMISSIONS_COLLECTION`
- `LOG_FILE`

## Local Run Without Docker

This is the simplest way to work on the project locally.

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Set `MONGO_URI` in `.env` to either:

- local Mongo on the host:

```env
MONGO_URI=mongodb://feedbot:password@localhost:27017/feed_database?authSource=feed_database
```

- remote Mongo:

```env
MONGO_URI=mongodb://user:password@mongo-host:27017/feed_database?authSource=feed_database
```

4. Start the bot:

```bash
python bot.py
```

## Local Docker Run With External Or Host Mongo

[`docker-compose.yml`](./docker-compose.yml) starts only the bot container.

It expects `MONGO_URI` from `.env`.

Examples:

- host Mongo from inside the container:

```env
MONGO_URI=mongodb://feedbot:password@host.docker.internal:27017/feed_database?authSource=feed_database
```

- remote Mongo:

```env
MONGO_URI=mongodb://user:password@mongo-host:27017/feed_database?authSource=feed_database
```

Run:

```bash
docker compose up --build
```

## Local Docker Run With Bot And Mongo Together

[`docker-compose.mongo.yml`](./docker-compose.mongo.yml) is an overlay file.

It adds a local Mongo container and overrides the bot container so it connects to `mongo:27017`.

Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.mongo.yml up --build
```

In this mode:

- Mongo runs in a local container
- bot runs in a local container
- Mongo data is stored in the `mongo_main_data` Docker volume
- user creation is handled by [`docker/mongo-init.js`](./docker/mongo-init.js)

## Production Deployment

Production deployment uses GitHub Actions, GHCR, and Docker Stack.

Files involved:

- [`Dockerfile`](./Dockerfile)
- [`docker-stack.yml`](./docker-stack.yml)
- [`.github/workflows/publish.yml`](./.github/workflows/publish.yml)

How it works:

1. Push to `main`
2. GitHub Actions builds the Docker image
3. The image is pushed to `ghcr.io/denikryt/feed-bot`
4. The workflow deploys the stack to the server over SSH
5. The stack starts only the `app` service
6. Mongo is external in production and is provided through `MONGO_URI`

Required GitHub secrets:

- `MONGO_URI`
- `MONGO_DB`
- `DISCORD_TOKEN`
- `ALLOWED_GUILD_IDS`
- `MONGO_MESSAGE_MAPPING_COLLECTION`
- `MONGO_GUILD_ROUTES_COLLECTION`
- `MONGO_GUILD_PERMISSIONS_COLLECTION`
- `LOG_FILE`
- `DEPLOY_SSH_PRIVATE_KEY`

## Which Mode To Use

Use:

- `python bot.py` for normal local development
- `docker compose up --build` if you want the bot in a container but Mongo outside it
- `docker compose -f docker-compose.yml -f docker-compose.mongo.yml up --build` if you want both bot and Mongo in Docker
- GitHub Actions on `main` for production deployment
