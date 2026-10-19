"""Create the relational AquaSat storage and forecast schema."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reservoirs",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("capacity_mcft", sa.Integer(), nullable=False),
    )
    op.create_table(
        "storage_observations",
        sa.Column("reservoir", sa.String(length=64), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("storage_mcft", sa.Float()),
        sa.Column("metadata_json", sa.Text()),
        sa.ForeignKeyConstraint(["reservoir"], ["reservoirs.slug"]),
        sa.PrimaryKeyConstraint("reservoir", "observation_date", "source"),
    )
    op.create_index(
        "ix_storage_observations_date_source",
        "storage_observations",
        ["observation_date", "source"],
    )
    op.create_table(
        "forecasts",
        sa.Column("reservoir", sa.String(length=64), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("origin_date", sa.Date()),
        sa.Column("horizon_days", sa.Integer()),
        sa.Column("yhat", sa.Float()),
        sa.Column("yhat_lower", sa.Float()),
        sa.Column("yhat_upper", sa.Float()),
        sa.Column("generated_at_utc", sa.String(length=40)),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["reservoir"], ["reservoirs.slug"]),
        sa.PrimaryKeyConstraint("reservoir", "forecast_date", "model"),
    )
    op.create_index("ix_forecasts_date", "forecasts", ["forecast_date"])
    op.create_table(
        "calibration_metrics",
        sa.Column("reservoir", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["reservoir"], ["reservoirs.slug"]),
        sa.PrimaryKeyConstraint("reservoir"),
    )
    op.create_table(
        "source_sync",
        sa.Column("source_file", sa.String(length=120), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("synced_at_utc", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("source_file"),
    )
    reservoirs = sa.table(
        "reservoirs",
        sa.column("slug", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("capacity_mcft", sa.Integer()),
    )
    op.bulk_insert(reservoirs, [
        {"slug": "poondi", "display_name": "Poondi", "capacity_mcft": 3231},
        {"slug": "cholavaram", "display_name": "Cholavaram", "capacity_mcft": 1081},
        {"slug": "puzhal", "display_name": "Puzhal (Redhills)", "capacity_mcft": 3300},
        {"slug": "chembarambakkam", "display_name": "Chembarambakkam", "capacity_mcft": 3645},
        {"slug": "veeranam", "display_name": "Veeranam", "capacity_mcft": 1465},
    ])


def downgrade() -> None:
    op.drop_table("source_sync")
    op.drop_table("calibration_metrics")
    op.drop_index("ix_forecasts_date", table_name="forecasts")
    op.drop_table("forecasts")
    op.drop_index("ix_storage_observations_date_source", table_name="storage_observations")
    op.drop_table("storage_observations")
    op.drop_table("reservoirs")
