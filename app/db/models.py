"""Database tables: one row per run, many step rows per run."""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal: Mapped[str] = mapped_column(Text)
    # pending -> running -> completed | failed | timed_out | max_steps_reached
    status: Mapped[str] = mapped_column(String(32), default="pending")
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cost awareness: total tokens used across every model call in this run.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    steps: Mapped[list["Step"]] = relationship(
        back_populates="run", order_by="Step.step_number", cascade="all, delete-orphan"
    )


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    step_number: Mapped[int] = mapped_column(Integer)
    # tool_call | tool_result | model_message
    type: Mapped[str] = mapped_column(String(32))
    # JSON so a tool call can be stored as {"tool": ..., "input": {...}}
    # and a message/result as a plain string.
    content: Mapped[dict | str] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="steps")
