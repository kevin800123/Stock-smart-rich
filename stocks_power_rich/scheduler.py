"""可選每日排程：用 APScheduler 在指定時間自動跑一鍵更新（需程式開著）。

另附 Windows 工作排程器設定（見 README），作為程式未開也能跑的備援。
"""
from apscheduler.schedulers.background import BackgroundScheduler


def parse_schedule_time(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def build_trigger_kwargs(s: str) -> dict:
    h, m = parse_schedule_time(s)
    return {"hour": h, "minute": m}


def start_scheduler(job, schedule_time: str) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Taipei")
    sched.add_job(job, "cron", **build_trigger_kwargs(schedule_time), id="daily_update", replace_existing=True)
    sched.start()
    return sched
