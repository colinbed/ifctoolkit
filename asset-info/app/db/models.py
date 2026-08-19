from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    models: Mapped[list["IfcModel"]] = relationship(back_populates="project")


class IfcModel(Base):
    __tablename__ = "ifc_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    project: Mapped[Project] = relationship(back_populates="models")


class Storey(Base):
    __tablename__ = "storeys"
    id: Mapped[int] = mapped_column(primary_key=True)
    ifc_model_id: Mapped[int] = mapped_column(ForeignKey("ifc_models.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))


class InformationObject(Base):
    __tablename__ = "information_objects"
    id: Mapped[int] = mapped_column(primary_key=True)
    ifc_model_id: Mapped[int] = mapped_column(ForeignKey("ifc_models.id", ondelete="CASCADE"))
    storey_id: Mapped[int | None] = mapped_column(ForeignKey("storeys.id", ondelete="SET NULL"))
    global_id: Mapped[str] = mapped_column(String(64), unique=True)
    object_type: Mapped[str] = mapped_column(String(255))


class ObjectAttribute(Base):
    __tablename__ = "object_attributes"
    id: Mapped[int] = mapped_column(primary_key=True)
    information_object_id: Mapped[int] = mapped_column(ForeignKey("information_objects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    value: Mapped[str | None] = mapped_column(Text)


class ObjectRelationship(Base):
    __tablename__ = "object_relationships"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_object_id: Mapped[int] = mapped_column(ForeignKey("information_objects.id", ondelete="CASCADE"))
    target_object_id: Mapped[int] = mapped_column(ForeignKey("information_objects.id", ondelete="CASCADE"))
    relationship_type: Mapped[str] = mapped_column(String(255))
