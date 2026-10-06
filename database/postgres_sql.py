import os
from functools import lru_cache
from urllib.parse import quote

import psycopg2
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()


@lru_cache(maxsize=4)
def get_engine(database: str | None = None):
    """
    Create a SQLAlchemy PostgreSQL engine.

    Uses Supabase PostgreSQL credentials from .env.
    """

    database = database or os.getenv("POSTGRES_DATABASE")

    host = os.getenv("POSTGRES_HOST")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")

    if not host:
        raise RuntimeError("Missing POSTGRES_HOST in .env")

    if not database:
        raise RuntimeError("Missing POSTGRES_DATABASE in .env")

    if not user:
        raise RuntimeError("Missing POSTGRES_USER in .env")

    if not password:
        raise RuntimeError("Missing POSTGRES_PASSWORD in .env")

    # Encode username/password so special characters
    # such as @, :, / etc. don't break the connection URL.
    encoded_user = quote(user, safe="")
    encoded_password = quote(password, safe="")

    connection_string = (
        f"postgresql+psycopg2://"
        f"{encoded_user}:{encoded_password}"
        f"@{host}:{port}/{database}"
        f"?sslmode=require"
    )

    return create_engine(
        connection_string,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        pool_size=5, max_overflow=5, pool_timeout=5,
    )


def create_database():
    """
    Create the PostgreSQL database if it doesn't already exist.

    Supabase normally already provides the 'postgres' database,
    so this function will usually just confirm that it exists.
    """

    target_db = os.getenv("POSTGRES_DATABASE")
    host = os.getenv("POSTGRES_HOST")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")

    if not target_db:
        raise RuntimeError("Missing POSTGRES_DATABASE in .env")

    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            database="postgres",
            user=user,
            password=password,
            sslmode="require"
        )

        connection.autocommit = True

        cursor = connection.cursor()

        try:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (target_db,)
            )

            if cursor.fetchone():
                print(
                    f"Database '{target_db}' already exists."
                )
            else:
                print(
                    f"Database '{target_db}' does not exist. "
                    f"Creating..."
                )

                # Database names cannot safely be passed as
                # normal SQL parameters, so validate it first.
                if not target_db.replace("_", "").isalnum():
                    raise ValueError(
                        "Invalid PostgreSQL database name."
                    )

                cursor.execute(
                    f'CREATE DATABASE "{target_db}"'
                )

                print(
                    f"Database '{target_db}' created successfully."
                )

        finally:
            cursor.close()
            connection.close()

    except Exception as exc:
        print(f"Failed to create database: {exc}")
        raise


def test_connection():
    """
    Test the PostgreSQL connection.
    """

    engine = get_engine()

    with engine.connect() as connection:
        result = connection.exec_driver_sql(
            "SELECT version();"
        )

        version = result.scalar()

    print("PostgreSQL connection successful.")
    print(f"PostgreSQL version: {version}")


if __name__ == "__main__":
    print("=" * 80)
    print("POSTGRESQL CONNECTION TEST")
    print("=" * 80)

    test_connection()
