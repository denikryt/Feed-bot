# Feed Bot

Discord bot with MongoDB-backed routing and message mapping.

## What This Project Does

Feed Bot mirrors messages from regular Discord channels into one or more configured feed channels inside the same guild.

It stores routing and mirrored-message metadata in MongoDB, so the bot can:

- remember which feed channels are configured for each guild
- avoid duplicate mirrored messages after restarts
- update mirrored messages when the source message is edited
- remove mirrored messages when the source message is deleted
- keep guild permission settings for who can manage feed routing

In practice, the result is a read-only feed channel where messages from selected source channels are reposted with channel context and tracked in MongoDB.

## Minimal Setup

Minimum requirements:

- a Discord bot token
- at least one allowed guild id
- a reachable MongoDB instance

Minimum `.env` values:

```env
DISCORD_TOKEN=...
ALLOWED_GUILD_IDS=123456789012345678
MONGO_URI=mongodb://feedbot:password@localhost:27017/feed_database?authSource=feed_database
MONGO_DB=feed_database
```

For the Docker mode where the bot runs in a container and MongoDB runs on the host, also set:

```env
MONGO_URI_DOCKER=mongodb://feedbot:password@host.docker.internal:27017/feed_database?authSource=feed_database
```

## Expected Result

After startup:

- the bot connects to Discord
- application commands are synced
- MongoDB indexes are created automatically

After you configure a feed channel and send a message in a source channel:

- the message appears in the configured feed channel
- a route record is stored in `guild_routes`
- a mirrored message record is stored in `message_mappings`

## Configuration

Create `.env` from `.env.example` and fill in the required values.

Main variables:

- `DISCORD_TOKEN`
- `ALLOWED_GUILD_IDS`
- `MONGO_URI`
- `MONGO_URI_DOCKER`
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

It expects `MONGO_URI_DOCKER` from `.env`.

`MONGO_URI=...localhost:27017...` works for `python bot.py` on the host, but it does not work inside the container because `localhost` there means the container itself.

On Linux, the host MongoDB service must also be reachable from the Docker host gateway address. If MongoDB is bound only to `127.0.0.1`, or if the firewall blocks `27017/tcp`, the container will not be able to reach it through `host.docker.internal`.

Examples:

- host Mongo from inside the container:

```env
MONGO_URI_DOCKER=mongodb://feedbot:password@host.docker.internal:27017/feed_database?authSource=feed_database
```

- remote Mongo:

```env
MONGO_URI_DOCKER=mongodb://user:password@mongo-host:27017/feed_database?authSource=feed_database
```

Run:

```bash
docker compose up --build
```

Linux host notes:

- MongoDB must listen on an address reachable from Docker, not only on `127.0.0.1`
- if `ufw` is enabled, allow `27017/tcp` from your LAN and Docker private subnets

Example `ufw` rules:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 27017 proto tcp
sudo ufw allow from 172.16.0.0/12 to any port 27017 proto tcp
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

Required GitHub variables:

- `DEPLOY_USER`
- `DEPLOY_HOST`

## Which Mode To Use

Use:

- `python bot.py` for normal local development
- `docker compose up --build` if you want the bot in a container but Mongo outside it; set `MONGO_URI_DOCKER` for this mode
- `docker compose -f docker-compose.yml -f docker-compose.mongo.yml up --build` if you want both bot and Mongo in Docker
- GitHub Actions on `main` for production deployment
