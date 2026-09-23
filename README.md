# DBT Revised

For a localhost-only demonstration, provide your private `.env` file in this directory and start Docker Desktop's Linux engine. Keep `.env` out of Git.

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python manage.py migrate --noinput
```

Open `http://localhost:8000` and check `http://localhost:8000/health/ready/`. Subsequent starts can use `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build`.

The local override binds Django to loopback, defers the large embedding model download until needed, skips TLS Nginx, and uses a pinned legacy MinIO image. It is for local use only. Public deployment requires separate domain, TLS, secret, and maintained object-storage configuration.
