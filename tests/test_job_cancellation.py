"""User cancellation must stop real work and survive queue/restart races."""
import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

app_module = pytest.importorskip("app")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "jobs", {})
    monkeypatch.setattr(app_module, "job_queue", asyncio.PriorityQueue())
    monkeypatch.setattr(app_module, "_admitting", set())
    monkeypatch.setattr(app_module, "_running_jobs", set())
    monkeypatch.setattr(app_module, "_draining", False)
    monkeypatch.setattr(app_module, "_stopping", False)
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)
    return tmp_path


def job(root, jid="cancel-me", status="queued"):
    directory = root / jid
    directory.mkdir()
    (directory / app_module._RESUME_FILE).write_text(json.dumps({
        "cmd": [sys.executable, "main.py"], "priority": 2,
        "user_id": "owner", "reservation_id": "reserved-minutes",
    }))
    record = {"status": status, "logs": [], "user_id": "owner",
              "reservation_id": "reserved-minutes", "output_dir": str(directory),
              "cmd": [sys.executable, "-c", "import time; time.sleep(60)"],
              "env": dict(os.environ)}
    app_module.jobs[jid] = record
    if status == "queued":
        app_module._enqueue_job(jid)
    return record


def test_queued_cancel_removes_only_that_job_and_balances_queue(env):
    cancelled = job(env)
    other = job(env, "keep-me")

    async def scenario():
        result = await app_module.cancel_job("cancel-me", None)
        assert result["status"] == "cancelled"
        assert app_module.job_queue.qsize() == 1
        assert (await app_module.job_queue.get())[2] == "keep-me"
        app_module.job_queue.task_done()
        await asyncio.wait_for(app_module.job_queue.join(), 1)
    asyncio.run(scenario())
    assert cancelled["status"] == "cancelled"
    assert other["status"] == "queued"
    assert not app_module._should_auto_retry(cancelled)
    assert not (env / "cancel-me" / app_module._RESUME_FILE).exists()


def test_cancel_survives_restart_without_recovering_partial_metadata(env):
    job(env)
    (env / "cancel-me" / "video_metadata.json").write_text(json.dumps({"shorts": []}))
    asyncio.run(app_module.cancel_job("cancel-me", None))
    app_module.jobs.clear()
    app_module._recover_jobs_from_disk()
    assert "cancel-me" not in app_module.jobs
    record = app_module._job_view_from_disk("cancel-me")
    assert record["status"] == "cancelled"
    assert record["user_id"] == "owner"
    # Even a stale instance writing its manifest again cannot revive it.
    (env / "cancel-me" / app_module._RESUME_FILE).write_text(json.dumps({"cmd": ["x"]}))
    assert app_module._resume_interrupted_jobs() == set()
    assert "cancel-me" not in app_module.jobs


def test_cancel_checks_owner_before_writing_marker(env, monkeypatch):
    record = job(env)
    monkeypatch.setattr(app_module, "BILLING_ENABLED", True)
    monkeypatch.setattr(app_module, "_user_from_request", AsyncMock(
        return_value=SimpleNamespace(id="someone-else")))
    with pytest.raises(HTTPException) as error:
        asyncio.run(app_module.cancel_job("cancel-me", None))
    assert error.value.status_code == 404
    assert record["status"] == "queued"
    assert not app_module._job_cancelled("cancel-me")


def test_cancel_releases_reserved_minutes_and_is_repeatable(env, monkeypatch):
    job(env)
    release = AsyncMock()
    monkeypatch.setattr(app_module, "BILLING_ENABLED", True)
    monkeypatch.setattr(app_module, "_user_from_request", AsyncMock(
        return_value=SimpleNamespace(id="owner")))
    monkeypatch.setattr(app_module, "cloud", SimpleNamespace(
        metering=SimpleNamespace(release_reservation=release)), raising=False)

    async def scenario():
        await app_module.cancel_job("cancel-me", None)
        await app_module.cancel_job("cancel-me", None)
    asyncio.run(scenario())
    assert release.await_count == 2
    release.assert_awaited_with("reserved-minutes")


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_finished_jobs_are_left_alone(env, status):
    record = job(env, status=status)
    result = asyncio.run(app_module.cancel_job("cancel-me", None))
    assert result["status"] == status
    assert record["status"] == status
    assert not app_module._job_cancelled("cancel-me")


def test_cancelling_admission_does_not_launch_it_or_block_next_job(env, monkeypatch):
    job(env)
    job(env, "keep-me")
    ran = []

    async def scenario():
        semaphore = asyncio.Semaphore(0)
        monkeypatch.setattr(app_module, "concurrency_semaphore", semaphore)
        monkeypatch.setattr(app_module, "_wait_for_shared_gpu", AsyncMock())
        async def wrapper(jid):
            ran.append(jid)
            app_module._running_jobs.discard(jid)
            semaphore.release()
            app_module.job_queue.task_done()
        monkeypatch.setattr(app_module, "run_job_wrapper", wrapper)
        worker = asyncio.create_task(app_module.process_queue())
        try:
            for _ in range(100):
                if "cancel-me" in app_module._admitting:
                    break
                await asyncio.sleep(0.01)
            assert "cancel-me" in app_module._admitting
            await app_module.cancel_job("cancel-me", None)
            semaphore.release()
            await asyncio.wait_for(app_module.job_queue.join(), 2)
            assert ran == ["keep-me"]
            assert not app_module._admitting
            assert semaphore._value == 1
        finally:
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
    asyncio.run(scenario())


def test_running_cancel_terminates_pipeline_and_releases_slot(env, monkeypatch):
    record = job(env)
    real_popen = subprocess.Popen
    processes = []
    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(app_module.subprocess, "Popen", popen)
    monkeypatch.setattr(app_module, "_paid_proxy_budget_exceeded", AsyncMock(return_value=False))
    delivery = AsyncMock()
    monkeypatch.setattr(app_module, "_deliver_job", delivery)

    async def scenario():
        semaphore = asyncio.Semaphore(0)
        monkeypatch.setattr(app_module, "concurrency_semaphore", semaphore)
        await app_module.job_queue.get()
        app_module._running_jobs.add("cancel-me")
        task = asyncio.create_task(app_module.run_job_wrapper("cancel-me"))
        try:
            for _ in range(100):
                if processes:
                    break
                await asyncio.sleep(0.01)
            assert processes
            await app_module.cancel_job("cancel-me", None)
            await asyncio.wait_for(task, 8)
            await asyncio.wait_for(app_module.job_queue.join(), 1)
            assert processes[0].poll() is not None
            assert record["status"] == "cancelled"
            assert not app_module._running_jobs
            assert semaphore._value == 1
            delivery.assert_not_awaited()
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, 9)
                    process.wait()
            if not task.done():
                task.cancel()
    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "linux", reason="uses /proc to detect zombie children")
def test_stop_kills_descendants_even_when_parent_exits_first(env):
    child_pid = env / "child.pid"
    source = ("import subprocess,sys,time; "
              "p=subprocess.Popen([sys.executable,'-c',"
              "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
              f"open({str(child_pid)!r},'w').write(str(p.pid)); time.sleep(60)")
    process = subprocess.Popen([sys.executable, "-c", source], start_new_session=True)

    async def scenario():
        for _ in range(100):
            if child_pid.exists():
                break
            await asyncio.sleep(0.01)
        assert child_pid.exists()
        await app_module._stop_job_process(process)
        pid = int(child_pid.read_text())
        # Linux may leave the orphan as a zombie until PID 1 reaps it.
        for _ in range(100):
            try:
                state = open(f"/proc/{pid}/stat").read().split()[2]
            except FileNotFoundError:
                return
            if state == "Z":
                return
            await asyncio.sleep(0.01)
        pytest.fail("pipeline child still running")
    try:
        asyncio.run(scenario())
    finally:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
        process.wait()


def test_unknown_job_returns_404(env):
    with pytest.raises(HTTPException) as error:
        asyncio.run(app_module.cancel_job("missing", None))
    assert error.value.status_code == 404
