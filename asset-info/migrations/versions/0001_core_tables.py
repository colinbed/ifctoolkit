"""Create core asset information tables."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("projects", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("ifc_models", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(255), nullable=False))
    op.create_table("storeys", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ifc_model_id", sa.Integer(), sa.ForeignKey("ifc_models.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(255), nullable=False))
    op.create_table("information_objects", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ifc_model_id", sa.Integer(), sa.ForeignKey("ifc_models.id", ondelete="CASCADE"), nullable=False), sa.Column("storey_id", sa.Integer(), sa.ForeignKey("storeys.id", ondelete="SET NULL"), nullable=True), sa.Column("global_id", sa.String(64), nullable=False, unique=True), sa.Column("object_type", sa.String(255), nullable=False))
    op.create_table("object_attributes", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("information_object_id", sa.Integer(), sa.ForeignKey("information_objects.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("value", sa.Text(), nullable=True))
    op.create_table("object_relationships", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source_object_id", sa.Integer(), sa.ForeignKey("information_objects.id", ondelete="CASCADE"), nullable=False), sa.Column("target_object_id", sa.Integer(), sa.ForeignKey("information_objects.id", ondelete="CASCADE"), nullable=False), sa.Column("relationship_type", sa.String(255), nullable=False))


def downgrade() -> None:
    for table in ("object_relationships", "object_attributes", "information_objects", "storeys", "ifc_models", "projects"):
        op.drop_table(table)
