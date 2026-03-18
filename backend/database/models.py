"""backend/database/models.py
SQLAlchemy ORM models – defines the full SQLite schema.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Company  (multi-tenancy root – one row per client)
# ---------------------------------------------------------------------------
class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(120))
    ceo_email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    ceo_password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    employees: Mapped[list["Employee"]] = relationship(
        "Employee", back_populates="company", cascade="all, delete-orphan"
    )
    reports: Mapped[list["AgentReport"]] = relationship(
        "AgentReport", back_populates="company", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        "Alert", back_populates="company", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Employee  (belongs to a Company)
# ---------------------------------------------------------------------------
class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    company: Mapped["Company"] = relationship("Company", back_populates="employees")
    submissions: Mapped[list["DailySubmission"]] = relationship(
        "DailySubmission", back_populates="employee", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# DailySubmission  (unchanged – scoped via employee → company)
# ---------------------------------------------------------------------------
class DailySubmission(Base):
    __tablename__ = "daily_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tasks_completed: Mapped[str | None] = mapped_column(Text)
    blockers: Mapped[str | None] = mapped_column(Text)
    next_day_plan: Mapped[str | None] = mapped_column(Text)
    mood: Mapped[int | None] = mapped_column(Integer)          # 1–5
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    employee: Mapped["Employee"] = relationship("Employee", back_populates="submissions")
    uploaded_files: Mapped[list["UploadedFile"]] = relationship(
        "UploadedFile", back_populates="submission", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# UploadedFile  (unchanged)
# ---------------------------------------------------------------------------
class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("daily_submissions.id", ondelete="CASCADE"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    submission: Mapped["DailySubmission"] = relationship(
        "DailySubmission", back_populates="uploaded_files"
    )


# ---------------------------------------------------------------------------
# AgentReport  (now scoped to a Company)
# ---------------------------------------------------------------------------
class AgentReport(Base):
    __tablename__ = "agent_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    company: Mapped["Company"] = relationship("Company", back_populates="reports")


# ---------------------------------------------------------------------------
# Alert  (now scoped to a Company)
# ---------------------------------------------------------------------------
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    # relationships
    company: Mapped["Company"] = relationship("Company", back_populates="alerts")