"""Copy a stopped SQLite ledger to an EMPTY migrated PostgreSQL database.

Reads URLs from environment, never prints credentials. Source remains untouched.
"""

import os

from sqlalchemy import MetaData, create_engine, select, text

from trade_agent.db import Base


def main():
    source = create_engine(os.environ["LEDGER_SOURCE_URL"])
    destination = create_engine(os.environ["TRADE_AGENT_DATABASE_URL"])
    old = MetaData()
    old.reflect(source)
    counts = {}
    with source.connect() as incoming, destination.begin() as outgoing:
        for table in Base.metadata.sorted_tables:
            if table.name not in old.tables:
                continue
            if outgoing.execute(select(table).limit(1)).first():
                raise RuntimeError(
                    f"Destination table {table.name} is not empty; refusing overwrite"
                )
            rows = [
                dict(row) for row in incoming.execute(select(old.tables[table.name])).mappings()
            ]
            if rows:
                outgoing.execute(table.insert(), rows)
            count = outgoing.execute(text(f'SELECT count(*) FROM "{table.name}"')).scalar_one()
            if count != len(rows):
                raise RuntimeError(f"Count mismatch for {table.name}")
            counts[table.name] = count
            for column in table.primary_key:
                if str(column.type) == "INTEGER":
                    outgoing.execute(
                        text(
                            f'SELECT setval(pg_get_serial_sequence(\'{table.name}\', \'{column.name}\'), COALESCE((SELECT MAX("{column.name}") FROM "{table.name}"), 1), EXISTS(SELECT 1 FROM "{table.name}"))'
                        )
                    )
    print({"migration": "verified", "source_preserved": True, "table_counts": counts})


if __name__ == "__main__":
    main()
