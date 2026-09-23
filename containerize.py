"""
containerize.py
Generates a concrete containerization plan for a detected ecosystem:
- a best-practice multi-stage Dockerfile
- docker-compose blocks for any auxiliary services detected (services.py)
- package/dependency management notes specific to that ecosystem
- cloud-managed-service equivalents for each auxiliary service (AWS/Azure/GCP)

All static templates — no network, no LLM call. These are meant as a
strong starting point to hand to an engineer, not a finished product.
"""

DOCKERFILES = {
    "Node.js / JavaScript": '''# syntax=docker/dockerfile:1
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build --if-present

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev
# Adjust this line to your actual build output (dist/, build/, etc.)
COPY --from=build /app/dist ./dist
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 3000
CMD ["node", "dist/index.js"]
''',
    "Python": '''# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/root/.local/bin:$PATH
COPY --from=build /root/.local /root/.local
COPY . .
RUN useradd -m appuser
USER appuser
EXPOSE 8000
# Adjust for your framework: uvicorn (FastAPI), gunicorn (Flask/Django), etc.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
''',
    "Java": '''# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn -B dependency:go-offline
COPY src ./src
RUN mvn -B package -DskipTests

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
''',
    "Go": '''# syntax=docker/dockerfile:1
FROM golang:1.22-alpine AS build
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /app/server .

FROM gcr.io/distroless/static-debian12
COPY --from=build /app/server /server
EXPOSE 8080
ENTRYPOINT ["/server"]
''',
    "Ruby": '''# syntax=docker/dockerfile:1
FROM ruby:3.3-slim AS build
WORKDIR /app
COPY Gemfile Gemfile.lock ./
RUN bundle install --deployment --without development test
COPY . .

FROM ruby:3.3-slim AS runtime
WORKDIR /app
COPY --from=build /app /app
RUN useradd -m appuser
USER appuser
EXPOSE 3000
CMD ["bundle", "exec", "rails", "server", "-b", "0.0.0.0"]
''',
    "PHP": '''# syntax=docker/dockerfile:1
FROM composer:2 AS vendor
WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --optimize-autoloader

FROM php:8.3-fpm-alpine AS runtime
WORKDIR /var/www/html
COPY --from=vendor /app/vendor ./vendor
COPY . .
EXPOSE 9000
CMD ["php-fpm"]
''',
    ".NET": '''# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY *.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app .
EXPOSE 8080
# Replace YourApp.dll with the actual published DLL name
ENTRYPOINT ["dotnet", "YourApp.dll"]
''',
}

PACKAGE_MGMT_NOTES = {
    "Node.js / JavaScript": [
        "Commit package-lock.json — use `npm ci` (not `npm install`) in the Dockerfile so builds are reproducible from the lockfile, not a fresh resolve.",
        "Add a .dockerignore with at least `node_modules` and `.git` — copying local node_modules into the image defeats the point of a clean install.",
        "Pin the Node major version in the base image (e.g. node:20-alpine, not node:alpine) so a Node release doesn't silently change your build.",
    ],
    "Python": [
        "Pin exact versions in requirements.txt (`==`, not `>=`), or use a lockfile-based tool (pip-tools, Poetry, uv) so the image is reproducible.",
        "Use `pip install --no-cache-dir` to keep the image slim; the multi-stage build above already keeps build tools out of the runtime layer.",
        "Add a .dockerignore with `__pycache__`, `.venv`, `.git`.",
    ],
    "Java": [
        "Copy only pom.xml first and run `dependency:go-offline` before copying source — this lets Docker cache the dependency layer separately from code changes, which is the single biggest Java build-time win.",
        "Use `-DskipTests` in the image build; run tests in CI before the image build, not inside it.",
        "Prefer a JRE (not JDK) base image for the runtime stage — smaller, and you don't need the compiler at runtime.",
    ],
    "Go": [
        "Go compiles to a single static binary — there's no runtime package manager step at all, which is why the runtime image can be `distroless` (no shell, no package manager, minimal attack surface — a genuine plus for a fintech's security posture).",
        "Commit go.sum and run `go mod download` before copying source, same layer-caching logic as the Java case.",
    ],
    "Ruby": [
        "Commit Gemfile.lock — `bundle install --deployment` refuses to run without a matching lockfile, which is exactly the reproducibility guarantee you want.",
        "Exclude development/test gem groups from the production image (`--without development test`).",
    ],
    "PHP": [
        "Commit composer.lock — `composer install --no-dev` alone doesn't guarantee reproducibility without it.",
        "`--optimize-autoloader` matters for production PHP performance, easy to forget.",
    ],
    ".NET": [
        "`dotnet restore` as its own layer before copying source, same caching logic as Java/Go.",
        "Publish in Release configuration — a Debug build in production is a common accidental performance/security issue.",
    ],
}

# service name -> managed equivalents per cloud
CLOUD_SERVICE_MAP = {
    "PostgreSQL": {"aws": "RDS for PostgreSQL (or Aurora PostgreSQL for higher scale)", "azure": "Azure Database for PostgreSQL", "gcp": "Cloud SQL for PostgreSQL (or AlloyDB for higher scale)"},
    "MySQL": {"aws": "RDS for MySQL (or Aurora MySQL)", "azure": "Azure Database for MySQL", "gcp": "Cloud SQL for MySQL"},
    "MongoDB": {"aws": "Amazon DocumentDB (Mongo-compatible) or MongoDB Atlas", "azure": "Azure Cosmos DB (Mongo API) or MongoDB Atlas", "gcp": "MongoDB Atlas (no native GCP-managed Mongo)"},
    "Redis": {"aws": "ElastiCache for Redis", "azure": "Azure Cache for Redis", "gcp": "Memorystore for Redis"},
    "RabbitMQ": {"aws": "Amazon MQ for RabbitMQ", "azure": "self-hosted, or migrate to Azure Service Bus", "gcp": "self-hosted, or migrate to Pub/Sub"},
    "Kafka": {"aws": "Amazon MSK", "azure": "Azure Event Hubs (Kafka-compatible) or HDInsight Kafka", "gcp": "self-hosted, or Confluent Cloud on GCP"},
    "Elasticsearch": {"aws": "Amazon OpenSearch Service", "azure": "self-hosted Elastic, or Azure AI Search as a different-but-related option", "gcp": "self-hosted Elastic, or Elastic Cloud on GCP"},
    "S3-compatible object storage": {"aws": "S3 (native)", "azure": "Azure Blob Storage", "gcp": "Cloud Storage"},
    "AWS SDK (S3/etc.)": {"aws": "native — already AWS", "azure": "rewrite calls for the Azure Blob Storage SDK", "gcp": "rewrite calls for the Cloud Storage SDK"},
    "SQL database (via Doctrine)": {"aws": "RDS (Postgres or MySQL, depending on your Doctrine driver config)", "azure": "Azure Database for PostgreSQL/MySQL", "gcp": "Cloud SQL"},
}

# service name -> the actual key used for it under `services:` in compose (must match COMPOSE_BLOCKS below)
COMPOSE_SERVICE_KEY = {
    "PostgreSQL": "postgres",
    "MySQL": "mysql",
    "MongoDB": "mongodb",
    "Redis": "redis",
    "RabbitMQ": "rabbitmq",
    "Kafka": "kafka",
    "Elasticsearch": "elasticsearch",
}

# service name -> docker-compose service block (2-space indented, ready to paste under `services:`)
COMPOSE_BLOCKS = {
    "PostgreSQL": '''  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: changeme
      POSTGRES_DB: appdb
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
''',
    "MySQL": '''  mysql:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: changeme
      MYSQL_DATABASE: appdb
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql
''',
    "MongoDB": '''  mongodb:
    image: mongo:7
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db
''',
    "Redis": '''  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
''',
    "RabbitMQ": '''  rabbitmq:
    image: rabbitmq:3-management-alpine
    ports:
      - "5672:5672"
      - "15672:15672"
''',
    "Kafka": '''  kafka:
    image: bitnami/kafka:3.6
    environment:
      KAFKA_CFG_NODE_ID: 0
      KAFKA_CFG_PROCESS_ROLES: controller,broker
      KAFKA_CFG_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
      KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: 0@kafka:9093
    ports:
      - "9092:9092"
''',
    "Elasticsearch": '''  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.0
    environment:
      discovery.type: single-node
      xpack.security.enabled: "false"
    ports:
      - "9200:9200"
''',
}

VOLUME_NAMES = {
    "PostgreSQL": "pg_data",
    "MySQL": "mysql_data",
    "MongoDB": "mongo_data",
}


def generate_dockerfile(ecosystem: str) -> str:
    return DOCKERFILES.get(ecosystem, f"# No Dockerfile template available yet for '{ecosystem}'.\n# Add one in containerize.py's DOCKERFILES dict.\n")


def package_mgmt_notes(ecosystem: str):
    return PACKAGE_MGMT_NOTES.get(ecosystem, [])


ECOSYSTEM_SLUG = {
    "Node.js / JavaScript": "node-app",
    "Python": "python-app",
    "Java": "java-app",
    "Go": "go-app",
    "Ruby": "ruby-app",
    "PHP": "php-app",
    ".NET": "dotnet-app",
}


def generate_compose(frameworks: list, aux_services: list) -> str:
    """
    frameworks: the list returned by detectors.detect_all() — one entry per
    detected manifest/service.
    aux_services: list of {name, category, ...} from services.detect_services()
    """
    lines = ["services:"]

    for entry in frameworks:
        from pathlib import Path
        manifest_path = Path(entry["manifest"])
        # Use the manifest's parent directory as the service name (e.g. service_a/package.json -> service_a);
        # falls back to a clean ecosystem-based slug for a manifest sitting at repo root.
        service_dir = manifest_path.parent.as_posix()
        if service_dir == ".":
            safe_name = ECOSYSTEM_SLUG.get(entry["ecosystem"], "app")
        else:
            safe_name = service_dir.lower().replace(" ", "-").replace("_", "-")
        lines.append(f"  {safe_name}:")
        lines.append(f"    build: {'./' + service_dir if service_dir != '.' else '.'}")
        lines.append(f"    # {entry['ecosystem']} — from {entry['manifest']}")
        lines.append("    ports:")
        lines.append('      - "8080:8080"  # adjust to the port this service actually listens on')
        if aux_services:
            lines.append("    depends_on:")
            for s in aux_services:
                key = COMPOSE_SERVICE_KEY.get(s["name"])
                if key and s["name"] in COMPOSE_BLOCKS:
                    lines.append(f"      - {key}")
        lines.append("")

    seen = set()
    for s in aux_services:
        name = s["name"]
        if name in seen or name not in COMPOSE_BLOCKS:
            continue
        seen.add(name)
        lines.append(COMPOSE_BLOCKS[name])

    volumes = [VOLUME_NAMES[s["name"]] for s in aux_services if s["name"] in VOLUME_NAMES]
    if volumes:
        lines.append("volumes:")
        for v in sorted(set(volumes)):
            lines.append(f"  {v}:")

    return "\n".join(lines)


def cloud_equivalents(service_name: str):
    return CLOUD_SERVICE_MAP.get(service_name, {"aws": "no mapping yet", "azure": "no mapping yet", "gcp": "no mapping yet"})
