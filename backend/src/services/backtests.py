import time
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.config.settings import REDIS_URL
from src.models import Backtest, Dataset, Job
from src.services.verification import detect_contamination, evaluate_retention
from src.utils.serialization import checksum

QUEUE_KEY = 'backtest:queue'


def _job_context(db, job):
    dataset = db.get(Dataset, job.dataset_id)
    if not dataset:
        raise HTTPException(409, '估计任务绑定的数据批次已不存在')
    model_name = str(job.params.get('model', ''))
    threshold = None
    for metric in job.result.get('metrics', []):
        if metric.get('label') == '目标可靠度里程':
            threshold = metric.get('value')
            break
    try:
        reliability = float(job.params.get('reliability', 0.9))
    except (ValueError, TypeError):
        reliability = 0.9
    fitting_assets = [str(row.get('asset')) for row in dataset.records
                      if str(row.get('model')) == model_name]
    return dataset, model_name, threshold, reliability, fitting_assets


def create(db, data, user):
    job = db.get(Job, data.job_id)
    if not job:
        raise HTTPException(404, '估计任务不存在')
    if job.status != 'completed':
        raise HTTPException(409, '仅已完成的估计结果可以发起留样核验')
    batch_no = data.batch_no.strip()
    if len(batch_no) < 2:
        raise HTTPException(422, '留样批次号去除空格后至少需要两个字符')
    # 同一估计并发收到不同留样批次，只认先受理的一批：在受理中的批次未结案前拒绝新批次。
    existing = db.scalar(select(Backtest).where(
        Backtest.job_id == job.id,
        Backtest.status.in_(('queued', 'running'))).order_by(Backtest.id))
    if existing:
        raise HTTPException(409, f'该估计已有受理中的留样批次 {existing.batch_no}；同一估计只认先受理的一批，须结案后才能受理新批次')

    dataset, model_name, threshold, reliability, fitting_assets = _job_context(db, job)
    try:
        # 拟合用过的部件或其他型号部件混入留样，整批退回（不登记）。
        clean_rows = detect_contamination(data.records, model_name, fitting_assets)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if threshold is None:
        raise HTTPException(409, '估计结果缺少目标可靠度里程，无法核验')

    name = data.name.strip()
    if len(name) < 2:
        name = f'{job.name} · 留样核验 {batch_no}'[:160]
    snapshot = [dict(row) for row in clean_rows]
    backtest = Backtest(
        job_id=job.id, name=name, batch_no=batch_no,
        sample_checksum=checksum(snapshot), records=snapshot,
        status='queued', attempts=0, result={}, error='', approved=False,
        created_by=user.id, created_at=time.time())
    db.add(backtest)
    try:
        db.flush()
    except IntegrityError:
        # 并发请求同时通过应用层检查时，由部分唯一索引兜底，仍只保留先受理的一批。
        db.rollback()
        raise HTTPException(409, '该估计已有受理中的留样批次；同一估计只认先受理的一批，须结案后才能受理新批次') from None
    return backtest


def enqueue(backtest_id):
    try:
        from redis import Redis
        client = Redis.from_url(REDIS_URL)
        client.rpush(QUEUE_KEY, backtest_id)
        client.close()
    except Exception:
        pass  # SQL queued 状态是持久兜底，worker 会主动捞取。


def execute(db, backtest_id):
    backtest = db.get(Backtest, backtest_id)
    if not backtest or backtest.status != 'queued':
        return
    job = db.get(Job, backtest.job_id)
    if not job or job.status != 'completed':
        backtest.status = 'failed'
        backtest.error = '关联估计任务不可用，留样核验无法执行'
        backtest.finished_at = time.time()
        return
    backtest.status = 'running'
    backtest.attempts = (backtest.attempts or 0) + 1
    db.commit()
    try:
        _, model_name, threshold, reliability, _ = _job_context(db, job)
        result = evaluate_retention(backtest.records, model_name, threshold, reliability)
        result['job_id'] = job.id
        result['batch_no'] = backtest.batch_no
        result['sample_checksum'] = backtest.sample_checksum
        result['attempts'] = backtest.attempts
        backtest.result = result
        backtest.status = 'completed'
        backtest.error = ''
        backtest.finished_at = time.time()
    except Exception as exc:
        # 失败保留批次与试次，可回到原批次重试；重试不重复登记。
        backtest.status = 'failed'
        backtest.error = str(exc)[:990]
        backtest.finished_at = time.time()


def retry(db, backtest, user):
    if backtest.status != 'failed':
        raise HTTPException(409, '仅执行失败的留样核验可以重试')
    backtest.status = 'queued'
    backtest.error = ''
    backtest.finished_at = None
    db.flush()
    return backtest


def approve(db, backtest, user):
    if backtest.status != 'completed':
        raise HTTPException(409, '仅已完成的留样核验可以审批')
    if not backtest.result.get('target_met'):
        raise HTTPException(409, '达标率下界未达到估计的可靠度目标，不能批准')
    backtest.approved = True
    return backtest
