"""The existing Documents/Clips library, with Generated and Saved versions.
Campaign removal changes labels only. Saving writes a verified copy, never
moves an original or overwrites a different existing video.
"""
import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

VIDEO_TYPES = {'.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi'}

def file_id(relative):
    return hashlib.sha256(str(relative).encode()).hexdigest()

def category_name(name):
    return re.sub(r'[^\w .-]', '_', name or 'Unassigned').strip(' .') or 'Campaign'

def safe_name(name):
    if not isinstance(name, str): raise ValueError('Enter a video filename.')
    name = name.strip()
    if not name or len(name) > 180 or name in ('.', '..') or any(c in name for c in '/\\:') or any(ord(c) < 32 for c in name):
        raise ValueError('Use a filename up to 180 characters without slashes or colons.')
    if Path(name).suffix.lower() not in VIDEO_TYPES: name += '.mp4'
    return name

def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):value.update(part)
    return value.hexdigest()

class FolderLibrary:
    def __init__(self, root, store): self.root=Path(root); self.store=store

    def scan(self):
        if not self.root.is_dir(): return []
        rows=[]
        with self.store.lock:
            data=self.store.snapshot(); seen=set(data.get('library_seen', [])); changed=False
            for path in self.root.rglob('*'):
                try:
                    if path.suffix.lower() not in VIDEO_TYPES or not path.is_file() or not path.resolve().is_relative_to(self.root.resolve()):continue
                    rel=path.relative_to(self.root); parts=rel.parts
                    if any(part.startswith('.') for part in parts):continue
                    fid=file_id(rel.as_posix()); key='file/'+fid; stat=path.stat()
                    saved=any(part.casefold()=='saved' for part in parts[:-1])
                    if fid not in seen:
                        category=parts[0] if len(parts)>1 and parts[0].casefold() not in ('saved','generated','unassigned') else None
                        if category:
                            campaign=next((c for c in data['campaigns'] if c['name'].casefold()==category.casefold()),None)
                            if campaign is None:
                                import secrets
                                campaign={'id':secrets.token_hex(8),'name':category};data['campaigns'].append(campaign)
                            data['clips'][key]=campaign['id']
                        seen.add(fid);changed=True
                    rows.append({'job_id':'file:'+fid,'local_file_id':fid,'title':path.stem.replace('_',' '),
                        'status':'completed','clip_count':1,'video_url':'/api/local/library/media/'+fid,
                        'created_at':datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat(),
                        'duration_seconds':None,'has_source':False,'kind':'saved' if saved else 'generated',
                        'source_key':data.get('editable_sources',{}).get(rel.as_posix()),
                        'saved':saved,'filename':path.name,'relative_path':rel.as_posix()})
                except (OSError, ValueError):continue
            refs=data.get('saved_sources',{})
            valid={k:v for k,v in refs.items() if (self.root/v).resolve().is_relative_to(self.root.resolve()) and (self.root/v).is_file()}
            if valid!=refs:data['saved_sources']=valid;changed=True
            if changed:
                data['library_seen']=sorted(seen);self.store._write(data)
        rows.sort(key=lambda r:r['created_at'],reverse=True)
        return rows

    def locate(self, fid):
        row=next((r for r in self.scan() if r['local_file_id']==fid),None)
        if not row:raise ValueError('This video is no longer in Documents/Clips.')
        return self.root/row['relative_path'], row

    def save_copy(self, source, name, campaign_id=None, source_key=None):
        name=safe_name(name)
        with self.store.lock:
            data=self.store.snapshot()
            if not campaign_id and source_key:
                campaign_id = data['clips'].get(source_key)
                if not campaign_id and '/clip/' in source_key:
                    campaign_id = data['clips'].get(source_key.split('/clip/',1)[0] + '/project')
            campaign=next((c for c in data['campaigns'] if c['id']==campaign_id),None) if campaign_id else None
            if campaign_id and campaign is None:raise ValueError('This campaign no longer exists.')
            category=self.root/category_name(campaign['name'] if campaign else None)
            target=category/'Saved'/name
            if not target.resolve().is_relative_to(self.root.resolve()):raise ValueError('Invalid saved location.')
            target.parent.mkdir(parents=True,exist_ok=True)
            (category/'Generated').mkdir(exist_ok=True)
            original_hash=digest(Path(source))
            if target.exists():
                if digest(target)!=original_hash:raise ValueError('A different video already uses this filename. It was not overwritten.')
            else:
                fd,temp=tempfile.mkstemp(prefix='.',suffix='.saving',dir=target.parent)
                try:
                    import shutil
                    with os.fdopen(fd,'wb') as out, Path(source).open('rb') as inp:shutil.copyfileobj(inp,out)
                    if digest(Path(temp))!=original_hash:raise OSError('Saved copy could not be verified; the original is preserved.')
                    # Hard-link publication is atomic and cannot overwrite a racing writer.
                    try:os.link(temp,target)
                    except FileExistsError:
                        if digest(target)!=original_hash:raise ValueError('A different video already uses this filename. It was not overwritten.')
                finally:Path(temp).unlink(missing_ok=True)
            rel=target.relative_to(self.root).as_posix();fid=file_id(rel)
            seen=set(data.get('library_seen',[]));seen.add(fid);data['library_seen']=sorted(seen)
            if campaign_id:data['clips']['file/'+fid]=campaign_id
            if source_key:
                data.setdefault('saved_sources',{})[source_key]=rel
                origin=source_key
                if source_key.startswith('file/'):
                    origin=next((key for relative,key in data.get('editable_sources',{}).items() if file_id(relative)==source_key[5:]),source_key)
                if origin:data.setdefault('editable_sources',{})[rel]=origin
            self.store._write(data)
            return rel

class FileAssignment(BaseModel):
    file_id: str
    campaign_id: Optional[str] = None


def make_folder_router(library, billing_enabled):
    router=APIRouter(prefix='/api/local/library')
    def guard():
        if billing_enabled:raise HTTPException(status_code=404,detail='Not found')
    @router.get('')
    def listing():
        guard();return {'videos':library.scan(),'folder':'Documents/Clips'}
    @router.get('/media/{fid}')
    def media(fid:str):
        guard()
        try:path,row=library.locate(fid)
        except ValueError as e:raise HTTPException(status_code=404,detail=str(e)) from e
        return FileResponse(path,filename=row['filename'],content_disposition_type='inline')
    @router.post('/assign')
    def assign(body:FileAssignment):
        guard()
        try:
            library.locate(body.file_id)
            library.store.assign(['file/'+body.file_id],body.campaign_id)
            return library.store.snapshot()
        except ValueError as e:raise HTTPException(status_code=400,detail=str(e)) from e
    @router.post('/save')
    async def save(request:Request, name:str, campaign_id:Optional[str]=None, source_key:Optional[str]=None):
        guard()
        if source_key and len(source_key)>200:raise HTTPException(status_code=400,detail='Invalid source identity')
        # Rendered blob previews and ordinary files share the same verified save path.
        fd,temp=tempfile.mkstemp(suffix='.mp4');size=0
        try:
            with os.fdopen(fd,'wb') as out:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>2*1024**3:raise HTTPException(status_code=413,detail='Video exceeds 2 GB')
                    out.write(chunk)
            if not size:raise ValueError('The video is empty.')
            import asyncio
            relative=await asyncio.to_thread(library.save_copy,Path(temp),name,campaign_id,source_key)
            return {'saved_path':'Documents/Clips/'+relative,'store':library.store.snapshot()}
        except ValueError as e:raise HTTPException(status_code=400,detail=str(e)) from e
        finally:Path(temp).unlink(missing_ok=True)
    return router
