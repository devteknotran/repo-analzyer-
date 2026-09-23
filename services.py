"""
services.py
Scans dependency manifests for known client-library signatures (database
drivers, cache clients, message queue clients, search clients, object
storage SDKs) to figure out what auxiliary infrastructure each service
actually needs — not just "containerize the app" but "containerize the app
AND run these N supporting services alongside it."

Still fully offline: reads manifest files already in the repo, matches
against a static signature table. No network, no API tokens.
"""
import re
from pathlib import Path

SKIP_DIRS = {
    "node_modules", ".git", "vendor", "venv", ".venv", "dist", "build",
    "__pycache__", "target", ".terraform", "coverage",
}

# dependency name/path fragment (lowercase) -> (category, human-readable service name)
NODE_SIGNATURES = {
    "pg": ("database", "PostgreSQL"), "pg-promise": ("database", "PostgreSQL"),
    "mysql": ("database", "MySQL"), "mysql2": ("database", "MySQL"),
    "mongoose": ("database", "MongoDB"), "mongodb": ("database", "MongoDB"),
    "redis": ("cache", "Redis"), "ioredis": ("cache", "Redis"),
    "amqplib": ("queue", "RabbitMQ"),
    "kafkajs": ("queue", "Kafka"), "node-rdkafka": ("queue", "Kafka"),
    "@elastic/elasticsearch": ("search", "Elasticsearch"),
    "@aws-sdk/client-s3": ("storage", "S3-compatible object storage"),
    "aws-sdk": ("storage", "AWS SDK (S3/etc.)"),
}
PYTHON_SIGNATURES = {
    "psycopg2": ("database", "PostgreSQL"), "psycopg2-binary": ("database", "PostgreSQL"),
    "asyncpg": ("database", "PostgreSQL"), "psycopg": ("database", "PostgreSQL"),
    "pymysql": ("database", "MySQL"), "mysqlclient": ("database", "MySQL"),
    "mysql-connector-python": ("database", "MySQL"),
    "pymongo": ("database", "MongoDB"), "mongoengine": ("database", "MongoDB"),
    "redis": ("cache", "Redis"), "aioredis": ("cache", "Redis"),
    "pika": ("queue", "RabbitMQ"), "kombu": ("queue", "RabbitMQ"),
    "kafka-python": ("queue", "Kafka"), "confluent-kafka": ("queue", "Kafka"),
    "elasticsearch": ("search", "Elasticsearch"),
    "boto3": ("storage", "AWS SDK (S3/etc.)"),
}
JAVA_SIGNATURES = {
    "postgresql": ("database", "PostgreSQL"),
    "mysql-connector-java": ("database", "MySQL"), "mysql-connector-j": ("database", "MySQL"),
    "mongodb-driver": ("database", "MongoDB"), "mongodb-driver-sync": ("database", "MongoDB"),
    "jedis": ("cache", "Redis"), "lettuce-core": ("cache", "Redis"),
    "amqp-client": ("queue", "RabbitMQ"),
    "kafka-clients": ("queue", "Kafka"), "spring-kafka": ("queue", "Kafka"),
    "elasticsearch-rest-client": ("search", "Elasticsearch"),
}
GO_SIGNATURES = {
    "lib/pq": ("database", "PostgreSQL"), "jackc/pgx": ("database", "PostgreSQL"),
    "go-sql-driver/mysql": ("database", "MySQL"),
    "mongo-driver": ("database", "MongoDB"),
    "go-redis/redis": ("cache", "Redis"), "redis/go-redis": ("cache", "Redis"),
    "streadway/amqp": ("queue", "RabbitMQ"), "rabbitmq/amqp091-go": ("queue", "RabbitMQ"),
    "segmentio/kafka-go": ("queue", "Kafka"), "confluent-kafka-go": ("queue", "Kafka"),
}
RUBY_SIGNATURES = {
    "pg": ("database", "PostgreSQL"), "mysql2": ("database", "MySQL"),
    "mongo": ("database", "MongoDB"),
    "redis": ("cache", "Redis"),
    "bunny": ("queue", "RabbitMQ"), "ruby-kafka": ("queue", "Kafka"),
}
PHP_SIGNATURES = {
    "doctrine/dbal": ("database", "SQL database (via Doctrine)"),
    "predis/predis": ("cache", "Redis"),
    "php-amqplib/php-amqplib": ("queue", "RabbitMQ"),
}

ECOSYSTEM_SIGNATURES = {
    "Node.js / JavaScript": NODE_SIGNATURES,
    "Python": PYTHON_SIGNATURES,
    "Java": JAVA_SIGNATURES,
    "Go": GO_SIGNATURES,
    "Ruby": RUBY_SIGNATURES,
    "PHP": PHP_SIGNATURES,
}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_dep_names(manifest_path: Path, ecosystem: str):
    """Best-effort dependency name extraction per ecosystem's manifest format."""
    text = _read_text(manifest_path)
    names = []

    if ecosystem == "Node.js / JavaScript":
        import json
        try:
            data = json.loads(text)
            names += list((data.get("dependencies") or {}).keys())
            names += list((data.get("devDependencies") or {}).keys())
        except Exception:
            pass
    elif ecosystem == "Python":
        if manifest_path.name == "requirements.txt":
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^([A-Za-z0-9_\-\.]+)", line)
                if m:
                    names.append(m.group(1).lower())
        else:
            # pyproject.toml / Pipfile — loose regex over quoted package names
            names += re.findall(r'["\']([A-Za-z0-9_\-\.]+)["\']\s*=', text)
    elif ecosystem == "Java":
        names += re.findall(r"<artifactId>([\w\-\.]+)</artifactId>", text)
    elif ecosystem == "Go":
        names += re.findall(r"^\t([^\s]+)\s+v\d", text, re.MULTILINE)
    elif ecosystem == "Ruby":
        names += re.findall(r"gem\s+['\"]([\w\-]+)['\"]", text)
    elif ecosystem == "PHP":
        import json
        try:
            data = json.loads(text)
            names += list((data.get("require") or {}).keys())
        except Exception:
            pass

    return [n.lower() for n in names]


def detect_services(repo_path: str, frameworks: list):
    """
    frameworks: the list returned by detectors.detect_all() — reused here so
    we don't re-walk the filesystem from scratch for manifest discovery.
    Returns a list of unique {category, name, ecosystem, source_manifest} dicts.
    """
    repo = Path(repo_path).resolve()
    found = {}  # (category, name) -> dict, deduped

    for entry in frameworks:
        ecosystem = entry["ecosystem"]
        signatures = ECOSYSTEM_SIGNATURES.get(ecosystem)
        if not signatures:
            continue
        manifest_path = repo / entry["manifest"]
        dep_names = _extract_dep_names(manifest_path, ecosystem)

        for dep in dep_names:
            for sig_key, (category, service_name) in signatures.items():
                if sig_key in dep or dep in sig_key:
                    key = (category, service_name)
                    if key not in found:
                        found[key] = {
                            "category": category,
                            "name": service_name,
                            "ecosystem": ecosystem,
                            "detected_via": f"{dep} (in {entry['manifest']})",
                        }

    return list(found.values())
