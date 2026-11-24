from apscheduler.schedulers.background import BackgroundScheduler

# 与 Web 同进程；测试环境（ENV=test）不启动。
scheduler = BackgroundScheduler(timezone="UTC")


def register_jobs(sched):
    """注册全部定时任务（interval + cron）；演示快进走 run_all_jobs_once。"""
    from app.modules.scheduler import jobs

    jobs.register(sched)


def run_all_jobs_once():
    """演示控制台快进后立即执行全部 job（含仿真通知投递）。"""
    from app.modules.scheduler import jobs

    jobs.run_all_once()