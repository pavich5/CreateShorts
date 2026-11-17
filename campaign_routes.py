"""Self-host campaign labels; never move or delete media."""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from clip_identity import find_clip_by_index

class CampaignName(BaseModel):
    name: str

class CampaignAssignment(BaseModel):
    job_id: str
    clip_index: Optional[int] = Field(default=None, ge=0, strict=True)
    campaign_id: Optional[str] = None


def make_campaign_router(store, jobs, billing_enabled, on_list=None):
    router = APIRouter(prefix="/api/local/campaigns")

    def local_only():
        if billing_enabled:
            raise HTTPException(status_code=404, detail="Not found")

    @router.get("")
    def list_campaigns():
        local_only()
        if on_list: on_list()
        return store.snapshot()

    @router.post("")
    def create_campaign(body: CampaignName):
        local_only()
        try:
            return {"campaign": store.create(body.name)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/assign")
    def assign_campaign(body: CampaignAssignment):
        local_only()
        job = jobs.get(body.job_id)
        if not job or job.get("status") != "completed":
            raise HTTPException(status_code=404, detail="Completed video not found")
        clips = (job.get("result") or {}).get("clips") or []
        if body.clip_index is not None:
            clip = find_clip_by_index(clips, body.clip_index)
            if clip is None or clip.get('deleted'):
                raise HTTPException(status_code=404, detail="Short not found")
        indices = [(clip.get('index') if clip.get('index') is not None else position)
                   for position, clip in enumerate(clips) if not clip.get('deleted')]
        project_key = body.job_id + "/project"
        with store.lock:
            snapshot = store.snapshot()
            if body.clip_index is None:
                keys = [project_key]
                old = snapshot["clips"].get(project_key)
                # Setting a source video's campaign assigns its current shorts.
                # Removing it preserves any shorts moved to another campaign.
                keys += [body.job_id + "/clip/" + str(index) for index in indices
                         if body.campaign_id is not None or (old and snapshot["clips"].get(body.job_id + "/clip/" + str(index)) == old)]
            else:
                keys = [body.job_id + "/clip/" + str(body.clip_index)]
            try:
                store.assign(keys, body.campaign_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return store.snapshot()

    @router.patch("/{campaign_id}")
    def rename_campaign(campaign_id: str, body: CampaignName):
        local_only()
        try:
            store.rename(campaign_id, body.name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return store.snapshot()

    @router.delete("/{campaign_id}")
    def remove_campaign(campaign_id: str):
        local_only()
        try:
            store.remove(campaign_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return store.snapshot()

    return router
