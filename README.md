# wealthdock-server

Self-hostable backend for [wealthdock](https://github.com/wealthdock/wealthdock) — cross-device sync, bank-API integration, data storage, auth, and encryption of sensitive financial data. Useful entirely on its own as pure backend infrastructure, independent of the UI.

Part of the [wealthdock](https://github.com/wealthdock) organization — see the [org profile](https://github.com/wealthdock/.github) for how the repos fit together.

## Development Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Docker (for local Postgres).

```bash
uv sync --all-extras
cp .env.example .env
docker compose up -d
uv run alembic upgrade head
```

Run the dev server:

```bash
uv run uvicorn wealthdock_server.main:app --reload
```

Check it's alive:

```bash
curl http://localhost:8000/health
```

Run linting and type checking:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

Run the test suite:

```bash
uv run pytest
```

## Self-Hosting & Deployment

`wealthdock-server` can be deployed as self-hosted infrastructure using Docker and Docker Compose.

### Quick Start with Docker Compose

Before running the stack, configure the required environment variables in a `.env` file (copy `.env.example` to `.env`).

1. Build and bring up the containerized application, migration task, and database services:
   ```bash
   docker compose up -d
   ```
2. The Docker Compose setup automatically runs a one-shot `db-migration` container to apply database migrations (`alembic upgrade head`) before starting the web application container.

> [!NOTE]
> If you deploy the `wealthdock-server` Docker image directly using a container orchestrator (e.g. Kubernetes, ECS), you must run the database migrations (`alembic upgrade head`) as a separate task before starting the application container.

### Configuration

All settings are configured via environment variables. Create a `.env` file in the root directory (see `.env.example`):

- `POSTGRES_PASSWORD`: The password for the PostgreSQL database (required by Docker Compose).
- `DATABASE_URL`: Connection string for PostgreSQL (e.g. `postgresql+asyncpg://user:pass@host:port/db`). Note: if your password contains special characters (`@`, `/`, `:`, `#`), ensure it is URL-percent-encoded.
- `APP_ENV`: Application environment (`production` or `development`).
- `JWT_SECRET`: Secret key used to sign JWT authentication tokens (required).
- `JWT_ALGORITHM`: Signature algorithm (defaults to `HS256`).
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`: Expiry duration for authentication tokens (defaults to `60`).
- `CORS_ORIGINS`: Allowed origins (required), passed as a comma-separated list (e.g. `http://localhost:3000,https://app.wealthdock.com`) or JSON array.
- `ENCRYPTION_KEY`: 32-byte URL-safe base64 key for encrypting sensitive data at rest (required; both the app and the migration step fail to start without it).

## Key Management & Encryption

Sensitive financial data (account numbers, balances, bank credentials/tokens) is encrypted at rest at the application layer using AES-256 (via cryptography's Fernet implementation).

For self-hosted and production deployments:
1. Set the `ENCRYPTION_KEY` environment variable (or configure it in your `.env` file).
2. The key must be a 32-byte, URL-safe, base64-encoded string.
3. You can generate a new secure key by running:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
4. **Important**: Store this key securely. If this key is lost or modified, all previously encrypted data in the database will be unrecoverable.

## License

MIT — see [LICENSE](LICENSE).

