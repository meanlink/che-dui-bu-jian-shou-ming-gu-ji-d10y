import {backtests} from '../../api/entities.js';
import {session} from '../../stores/session.js';
import {table} from '../common/table.js';
import {esc,fmt,date,notify} from '../../utils/dom.js';
import {parseCSV} from '../../utils/csv.js';
import {BacktestStatus} from '../../constants/enums.js';
const pct=v=>(Number(v)*100).toFixed(2)+'%';
const btStatus=v=>'<span class="status '+esc(v)+'">'+esc(BacktestStatus[v]||v)+'</span>';
export async function backtestPanel(job){
 const list=await backtests.list(job.id);
 const canWrite=session.user.role!=='viewer';
 const latest=list[0];
 const detail=list.find(b=>b.status==='completed')||null;
 const failed=list.find(b=>b.status==='failed');
 return '<section class="panel backtest-panel"><div class="section-title"><h2>留样核验 · 回测</h2><span>独立留样批次验证建议检修里程</span></div>'
 +'<div class="bt-rule"><p>留样须为未参与拟合的独立部件；混入拟合用过的部件或其他型号部件，整批退回且不登记。同一估计并发收到不同批次时，只认先受理的一批。</p><p>到建议里程算达标，提前失效算未达标；提前停跟部件分别全按达标、全按未达标计算，给出全批达标率范围。仅下界达到估计的可靠度目标才可批准。</p></div>'
 +(canWrite?'<form id="backtest-form"><div class="parameter-grid"><label>留样批次号<input name="batch_no" required minlength="2" placeholder="例如：RT-202611-A"></label><label>上传留样 CSV 或 JSON<input type="file" id="bt-file" accept=".json,.csv"></label></div><label>留样记录 JSON<textarea name="records" rows="5" placeholder="粘贴 JSON 数组，或选择上方文件；字段 asset / model / km / failed" required></textarea><button type="submit" class="primary">发起留样核验 ↗</button><p class="hint">提交后由后台 worker 执行。受理中的批次结案前，不能再受理新批次。</p></form>':'<p class="hint">观察员可查看核验结果与审批依据，无法发起或重试。</p>')
 +(list.length?'<div class="bt-list"><div class="section-title"><h2>留样批次</h2><span>'+list.length+' 批</span></div>'+list.map(b=>'<article class="bt-item '+(b.id===latest.id?'active':'')+'" data-bt="'+b.id+'"><div><strong>'+esc(b.batch_no)+'</strong><p>'+esc(b.name)+'</p><small>'+(b.result?.counts?.total?('留样 '+b.result.counts.total+' 件 · '):'')+'试次 '+b.attempts+' · '+date(b.created_at)+' · SHA256 '+esc(b.sample_checksum.slice(0,16))+'…</small></div>'+btStatus(b.status)+(b.approved?'<span class="approval">已批准</span>':'')+'</article>').join('')+'</div>':'<p class="empty">尚无留样核验批次。</p>')
 +(failed&&canWrite?'<button class="button" id="bt-retry" data-retry="'+failed.id+'">重试失败批次（恢复原批，不重复登记）↻</button>':'')
 +(detail?renderDetail(detail):'')
 +'</section>';
}
function renderDetail(b){
 const r=b.result;
 const met=r.target_met;
 return '<div class="bt-result"><div class="section-title"><h2>'+esc(b.batch_no)+' · 核验结论</h2><span>建议里程 '+fmt(r.threshold_km)+' 公里 · 目标可靠度 '+pct(r.reliability_target)+'</span></div>'
 +'<p class="bt-summary '+(met?'ok':'no')+'">'+esc(r.summary)+'</p>'
 +'<div class="metrics">'+(r.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(m.label.indexOf('率')>=0?pct(m.value):fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div>'
 +'<div class="bt-range"><div><span>全批达标率范围</span><strong>'+pct(r.rate_lower)+' ~ '+pct(r.rate_upper)+'</strong></div><div class="verdict '+(met?'ok':'no')+'">'+(met?'下界达到可靠度目标，具备批准依据':'下界未达可靠度目标，不予批准')+'</div></div>'
 +'<div class="two-columns"><section><div class="section-title"><h2>审批依据</h2></div><ol class="questions">'+(r.basis||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'+(session.user.role==='admin'?(b.approved?'<p class="hint">该留样核验已获业务负责人批准。':met?'<button class="primary" id="bt-approve" data-approve="'+b.id+'">批准此核验结论</button>':'<button class="primary" disabled>下界未达标，禁止批准</button>'):'<p class="hint">仅业务负责人可批准。</p>')+'</section><section class="method-sheet compact"><p class="eyebrow">SAMPLE SNAPSHOT</p><h3>留样快照与逐件判定</h3><p>批次指纹 SHA256 '+esc(b.sample_checksum)+'</p>'+table(r.table?.columns||[],r.table?.rows||[])+'</section></div></div>';
}
export function bindBacktest(refresh,jobId){
 const file=document.querySelector('#bt-file');
 file?.addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;const text=await f.text(),records=f.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);document.querySelector('#backtest-form [name=records]').value=JSON.stringify(records,null,2);}catch(err){notify(err.message,true);}});
 const form=document.querySelector('#backtest-form');
 form?.addEventListener('submit',async e=>{e.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{let records;try{records=JSON.parse(new FormData(form).get('records'));}catch{throw new Error('留样记录不是合法 JSON');}await backtests.create({job_id:jobId,batch_no:new FormData(form).get('batch_no'),records});notify('留样核验已受理，后台执行中');await refresh();}catch(err){notify(err.message,true);}finally{button.disabled=false;}});
 document.querySelector('#bt-approve')?.addEventListener('click',async()=>{try{await backtests.approve(document.querySelector('#bt-approve').dataset.approve);notify('已批准该留样核验');await refresh();}catch(err){notify(err.message,true);}});
 document.querySelector('#bt-retry')?.addEventListener('click',async()=>{try{await backtests.retry(document.querySelector('#bt-retry').dataset.retry);notify('已恢复原批重新排队，未重复登记');await refresh();}catch(err){notify(err.message,true);}});
}
