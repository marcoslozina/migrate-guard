"""What a target's database looks like after a case: the watched tables, row by row."""

from __future__ import annotations

from dataclasses import dataclass, field

from migrate_guard.domain.value_objects.json_value import JsonValue

type Row = dict[str, JsonValue]  # logical column name -> normalized value


@dataclass(frozen=True)
class TableSnapshot:
    # Row key text (e.g. "sku=A1") -> the row. The key comes from the configured key columns.
    rows: dict[str, Row] = field(default_factory=dict)


@dataclass(frozen=True)
class StateSnapshot:
    tables: dict[str, TableSnapshot] = field(default_factory=dict)  # by logical table name

    def as_json(self) -> JsonValue:
        """The snapshot as one JSON document, so the response diff engine can compare it."""
        return {
            name: {key: dict(row) for key, row in table.rows.items()}
            for name, table in self.tables.items()
        }
