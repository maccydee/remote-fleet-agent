import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from remote_fleet_agent import jobs as jobs_module
from remote_fleet_agent.jobs import JobManager


@pytest.mark.asyncio
async def test_thirty_hosts_across_jobs_share_limit_and_preserve_partial_results(
    tmp_path,
):
    active = peak = 0

    async def worker(host, operation, sections):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            if host == "pc-3":
                raise ConnectionError("fixture unreachable host")
            partial = host == "pc-4"
            return {
                "status": "partial" if partial else "complete",
                "sections": {
                    "os": {
                        "status": "unknown" if partial else "collected",
                        "items": [],
                        "total": 0,
                        "truncated": False,
                    }
                },
            }
        finally:
            active -= 1

    manager = JobManager(tmp_path, "fixture", worker, concurrency=4)
    a = manager.start([f"pc-{n}" for n in range(15)], "inventory", ["os"])
    b = manager.start([f"pc-{n}" for n in range(15, 30)], "inventory", ["os"])
    first, second = await asyncio.gather(manager.wait(a["id"]), manager.wait(b["id"]))
    assert peak == 4
    assert first["status"] == "partial" and second["status"] == "complete"
    assert (
        sum(
            h["status"] == "complete"
            for job in (first, second)
            for h in job["hosts"].values()
        )
        == 28
    )
    assert manager.retry_hosts(a["id"])[1] == ["pc-3", "pc-4"]
    assert len(json.loads(manager._file(b["id"]).read_text())["hosts"]) == 15
    await manager.close()


@pytest.mark.asyncio
async def test_real_controller_crash_retains_finished_hosts_for_explicit_retry(
    tmp_path,
):
    code = """
import asyncio,sys
from pathlib import Path
from remote_fleet_agent.jobs import JobManager
async def main():
 async def worker(host,operation,sections):
  if host == 'finished': return {'status':'complete','sections':{'os':{'status':'collected','items':[]}}}
  print('worker-entered',flush=True)
  await asyncio.sleep(120)
 manager=JobManager(Path(sys.argv[1]),'fixture',worker,concurrency=1)
 job=manager.start(['finished','unfinished'],'inventory',['os'])
 print(job['id'],flush=True)
 await manager.wait(job['id'])
asyncio.run(main())
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(jobs_module.__file__).resolve().parents[1]),
        PYTHONDONTWRITEBYTECODE="1",
    )
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(tmp_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        job_id = (await asyncio.wait_for(child.stdout.readline(), 10)).decode().strip()
        assert (
            await asyncio.wait_for(child.stdout.readline(), 10)
        ).decode().strip() == "worker-entered"
        child.kill()
        await child.wait()
        manager = JobManager(tmp_path, "fixture", None)
        job = manager.get(job_id)
        assert job["status"] == "interrupted"
        assert job["hosts"]["finished"]["status"] == "complete"
        assert job["hosts"]["unfinished"]["status"] == "interrupted"
        assert manager.retry_hosts(job_id)[1] == ["unfinished"]
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
