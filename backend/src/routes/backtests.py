from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import Backtest, Job
from src.schemas.backtest import BacktestInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import backtests
from src.services.audit import record
from src.utils.serialization import backtest_dict
router = APIRouter(prefix='/backtests')
@router.get('')
def listing(job_id: int | None = None, db=Depends(get_db), u=Depends(current_user)):
    stmt = select(Backtest).order_by(Backtest.id.desc()).limit(100)
    if job_id is not None:
        stmt = select(Backtest).where(Backtest.job_id == job_id).order_by(Backtest.id.desc())
    return [backtest_dict(b) for b in db.scalars(stmt)]
@router.post('', status_code=201)
def create(data: BacktestInput, db=Depends(get_db), u=Depends(can_write)):
    b = backtests.create(db, data, u)
    record(db, u.email, 'backtest.create', {'id': b.id, 'job_id': b.job_id, 'batch_no': b.batch_no, 'rows': len(b.records)})
    db.commit()
    backtests.enqueue(b.id)
    return backtest_dict(b)
@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    b = db.get(Backtest, id)
    if not b:
        raise HTTPException(404, '留样核验不存在')
    return backtest_dict(b)
@router.post('/{id}/retry')
def retry(id: int, db=Depends(get_db), u=Depends(can_write)):
    b = db.get(Backtest, id)
    if not b:
        raise HTTPException(404, '留样核验不存在')
    b = backtests.retry(db, b, u)
    record(db, u.email, 'backtest.retry', {'id': id, 'attempts': b.attempts})
    db.commit()
    backtests.enqueue(b.id)
    return backtest_dict(b)
@router.post('/{id}/approve')
def approve(id: int, db=Depends(get_db), u=Depends(admin_only)):
    b = db.get(Backtest, id)
    if not b:
        raise HTTPException(404, '留样核验不存在')
    b = backtests.approve(db, b, u)
    record(db, u.email, 'backtest.approve', {'id': id, 'target_met': b.result.get('target_met')})
    db.commit()
    return backtest_dict(b)
