from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, Integer, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class Backtest(Base):
    __tablename__ = 'backtests'
    __table_args__ = (
        # 数据库级保证：同一估计至多一个受理中（queued/running）的留样批次。
        Index('ux_backtest_active_job', 'job_id', unique=True,
              sqlite_where=text("status IN ('queued','running')"),
              postgresql_where=text("status IN ('queued','running')")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    name: Mapped[str] = mapped_column(String(160))
    batch_no: Mapped[str] = mapped_column(String(120))
    sample_checksum: Mapped[str] = mapped_column(String(64))
    records: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
