import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report, Backtest
from src.services.business import analyze
from src.services.reports import deliver
from src.services import backtests
from src.services.audit import record
logging.basicConfig(level=logging.INFO)
def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=str(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()
def execute_backtest(backtest_id):
    with Session() as db:
        b=db.get(Backtest,backtest_id)
        if not b or b.status!='queued':return
        backtests.execute(db,backtest_id)
        if b.status=='completed':
            record(db,'worker','backtest.completed',{'id':b.id,'attempts':b.attempts,'target_met':b.result.get('target_met')})
        else:
            record(db,'worker','backtest.failed',{'id':b.id,'attempts':b.attempts,'error':b.error})
        db.commit()
def tick():
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    # SQL 排队状态兜底：Redis 不可用或消息丢失时，排队中的回测仍会被捞起，且同一行只登记一次。
    with Session() as db:
        pending=list(db.scalars(select(Backtest.id).where(Backtest.status=='queued').limit(3)))
    for backtest_id in pending:execute_backtest(backtest_id)
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled==True,Report.next_run<=time.time())):
            d=deliver(db,r)
            if d:record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id})
        db.commit()
def recover_interrupted():
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        # 回测中断后恢复原批：回到同一登记行重新排队，重试不重复登记。
        for b in db.scalars(select(Backtest).where(Backtest.status=='running')):
            b.status='queued';record(db,'worker','backtest.recovered',{'id':b.id,'attempts':b.attempts})
        db.commit()
def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    recover_interrupted()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop('analysis:queue',timeout=1)
            if item:execute(int(item[1]))
            item=r.blpop(backtests.QUEUE_KEY,timeout=1)
            if item:execute_backtest(int(item[1]))
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)
if __name__=='__main__':main()
