import sqlite3


def upgrade(conn: sqlite3.Connection):
    """Add immutable categorical rendering intent and readback evidence."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(policy_v2_edit_inferences)")
    }
    additions = (
        ("current_rendering_state_json", "TEXT"),
        ("target_rendering_state_json", "TEXT"),
        ("rendering_intent_json", "TEXT"),
        ("rendering_selector_version", "TEXT"),
    )
    for name, data_type in additions:
        if name not in columns:
            conn.execute(
                f"ALTER TABLE policy_v2_edit_inferences ADD COLUMN {name} {data_type}"
            )
