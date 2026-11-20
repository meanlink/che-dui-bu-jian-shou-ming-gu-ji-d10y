from pydantic import BaseModel, Field
class BacktestInput(BaseModel):
    name: str = Field(default='', max_length=160)
    job_id: int
    batch_no: str = Field(min_length=2, max_length=120)
    records: list[dict] = Field(min_length=1, max_length=5000)
