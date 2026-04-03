#!/usr/bin/env python3
# =============================================================================
# setup_alembic.py — Initialize Alembic Migrations
# Run once: python setup_alembic.py
# =============================================================================
import os, sys

ALEMBIC_INI = """[alembic]
script_location = alembic
file_template = %(year)d_%(month).2d_%(day).2d_%(rev)s_%(slug)s
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %%(levelname)-5.5s [%%(name)s] %%(message)s
datefmt = %%H:%%M:%%S
"""

ENV_PY = """import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

config = context.config
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    db_url = db_url.replace("postgresql+asyncpg", "postgresql")
    config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name:
    fileConfig(config.config_file_name)

from database import Base
import models
target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection,
                          target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
"""

SCRIPT_MAKO = '''"""${message}
Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

def upgrade(): ${upgrades if upgrades else "pass"}
def downgrade(): ${downgrades if downgrades else "pass"}
'''

def main():
    print("Setting up Alembic migrations...")
    with open("alembic.ini", "w") as f: f.write(ALEMBIC_INI)
    os.makedirs("alembic/versions", exist_ok=True)
    with open("alembic/__init__.py", "w") as f: f.write("")
    with open("alembic/env.py", "w") as f: f.write(ENV_PY)
    with open("alembic/script.py.mako", "w") as f: f.write(SCRIPT_MAKO)
    print("Done! Run: alembic revision --autogenerate -m 'initial' && alembic upgrade head")

if __name__ == "__main__":
    main()
