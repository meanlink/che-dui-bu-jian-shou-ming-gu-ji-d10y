"""留样核验：用独立留样批次验证估计给出的建议检修里程。

纯函数，不接触数据库与 Web 层，便于单元检查。
口径：
- 到建议里程仍正常（在役且观测里程达到建议里程）算达标；
- 在建议里程之前失效算未达标；
- 提前停跟（在役但观测里程不足建议里程）结果未知，
  分别按“全部达标”和“全部未达标”两种极端口径，给出全批达标率的上下界；
- 只有下界达到该估计的目标可靠度，才具备批准依据。
"""
import math

KM_MIN, KM_MAX = 1, 10_000_000


def _clean_rows(records):
    if not isinstance(records, list) or not records:
        raise ValueError('留样批次为空，无法核验建议检修里程')
    rows, assets = [], set()
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            raise ValueError(f'第 {index + 1} 条留样不是有效记录')
        try:
            asset = str(raw['asset']).strip()
            model = str(raw['model']).strip()
            if isinstance(raw['km'], bool) or isinstance(raw['failed'], bool):
                raise TypeError
            km = float(raw['km'])
            failed = float(raw['failed'])
        except (KeyError, ValueError, TypeError):
            raise ValueError(f'第 {index + 1} 条留样须含资产号、型号、正里程和0/1失效标记') from None
        if not asset:
            raise ValueError(f'第 {index + 1} 条留样资产号为空')
        if asset in assets:
            raise ValueError(f'留样资产号 {asset} 在批次内重复')
        if not math.isfinite(km) or not KM_MIN <= km <= KM_MAX:
            raise ValueError(f'留样 {asset} 的里程须为 {KM_MIN} 至 {KM_MAX} 公里的有限数值')
        if failed not in (0.0, 1.0):
            raise ValueError(f'留样 {asset} 的失效标记只能是 0 或 1')
        assets.add(asset)
        rows.append({'asset': asset, 'model': model, 'km': km, 'failed': int(failed)})
    return rows


def evaluate_retention(records, model_name, threshold_km, reliability_target):
    """对单个留样批次执行核验。

    ``fitting_assets`` 由服务层从拟合所用数据批次注入；
    混入拟合用过的部件或其他型号部件，整批退回（拒绝登记）。
    """
    rows = _clean_rows(records)
    if not model_name:
        raise ValueError('缺少核验对应的部件型号')
    try:
        threshold = float(threshold_km)
        target = float(reliability_target)
    except (ValueError, TypeError):
        raise ValueError('建议里程与目标可靠度必须是数值') from None
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError('建议检修里程缺失或非法，无法核验')
    if not math.isfinite(target) or not 0 < target < 1:
        raise ValueError('目标可靠度须介于 0 和 1 之间')

    total = len(rows)
    failures = sum(r['failed'] for r in rows)
    censored = total - failures
    confirmed_qualified = 0
    confirmed_failed = 0
    undetermined = 0
    detail = []
    for r in rows:
        if r['failed'] == 1:
            if r['km'] >= threshold:
                verdict = '达标'
                reason = '达到建议里程后才失效'
                confirmed_qualified += 1
            else:
                verdict = '未达标'
                reason = '未到建议里程提前失效'
                confirmed_failed += 1
        elif r['km'] >= threshold:
            verdict = '达标'
            reason = '在役且观测里程已达到建议里程'
            confirmed_qualified += 1
        else:
            verdict = '待定'
            reason = '提前停跟，未观测到建议里程'
            undetermined += 1
        detail.append({'asset': r['asset'], 'model': r['model'], 'km': round(r['km'], 1),
                       'failed': r['failed'], 'verdict': verdict, 'reason': reason})

    lower = confirmed_qualified / total
    upper = (confirmed_qualified + undetermined) / total
    approvable = lower >= target
    columns = [
        {'key': 'asset', 'label': '部件资产号'},
        {'key': 'model', 'label': '型号'},
        {'key': 'km', 'label': '观测运行里程'},
        {'key': 'failed', 'label': '失效1/在役0'},
        {'key': 'verdict', 'label': '核验判定'},
        {'key': 'reason', 'label': '判定依据'},
    ]
    metrics = [
        {'label': '留样总数', 'value': total, 'unit': '件'},
        {'label': '确认达标', 'value': confirmed_qualified, 'unit': '件'},
        {'label': '提前失效', 'value': confirmed_failed, 'unit': '件'},
        {'label': '提前停跟待定', 'value': undetermined, 'unit': '件'},
        {'label': '达标率下界', 'value': round(lower, 4)},
        {'label': '达标率上界', 'value': round(upper, 4)},
    ]
    basis = [
        f'建议检修里程 {threshold:,.0f} 公里，对应估计的目标可靠度 {target:.2%}。',
        f'留样 {total} 件：{failures} 件已失效、{censored} 件截止仍在役；其中 {undetermined} 件提前停跟、结论待定。',
        '提前停跟部件全按未达标计，达标率下界为 '
        f'{lower:.2%}（{confirmed_qualified}/{total}）；全按达标计，上界为 {upper:.2%}'
        f'（{confirmed_qualified + undetermined}/{total}）。',
        ('下界已达到目标可靠度，满足批准条件。' if approvable
         else f'下界 {lower:.2%} 低于目标可靠度 {target:.2%}，不满足批准条件，该检修里程不予批准。'),
    ]
    return {
        'summary': (f'{model_name} 留样 {total} 件核验：全批达标率区间 {lower:.2%} ~ {upper:.2%}，'
                    + ('下界达到可靠度目标，可批准。' if approvable else '下界未达可靠度目标，不可批准。')),
        'model': model_name,
        'threshold_km': round(threshold, 1),
        'reliability_target': round(target, 4),
        'counts': {'total': total, 'failures': failures, 'censored': censored,
                   'confirmed_qualified': confirmed_qualified,
                   'confirmed_failed': confirmed_failed, 'undetermined': undetermined},
        'rate_lower': round(lower, 4),
        'rate_upper': round(upper, 4),
        'target_met': approvable,
        'metrics': metrics,
        'table': {'columns': columns, 'rows': detail},
        'basis': basis,
    }


def detect_contamination(records, model_name, fitting_assets):
    """混样检测：拟合用过的部件或其他型号部件混入留样，整批退回。"""
    rows = _clean_rows(records)
    fitting = set(fitting_assets or ())
    foreign_models = sorted({r['model'] for r in rows if r['model'] != model_name})
    reused = sorted({r['asset'] for r in rows if r['asset'] in fitting})
    problems = []
    if foreign_models:
        problems.append('留样混入非本型号（' + '、'.join(foreign_models) + '）部件')
    if reused:
        problems.append('留样混入拟合已用过的部件：' + '、'.join(reused[:10])
                        + ('…' if len(reused) > 10 else ''))
    if problems:
        raise ValueError('留样整批退回：' + '；'.join(problems))
    return rows
