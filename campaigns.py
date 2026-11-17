"""Persistent campaign labels. Membership never moves or deletes video files."""
import json
import secrets
import threading
from pathlib import Path


class Campaigns:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            if not self.path.exists():
                return {'version': 1, 'campaigns': [], 'clips': {}}
            data = json.loads(self.path.read_text())
            if data.get('version') != 1 or not isinstance(data.get('campaigns'), list) or not isinstance(data.get('clips'), dict):
                raise ValueError('Campaign data could not be read. Your videos are preserved.')
            return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('.' + self.path.name + '.tmp')
        try:
            temporary.write_text(json.dumps(data, indent=2) + '\n')
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def clip_key(folder, file):
        # Folder names survive the switch from live job IDs to restored batch IDs.
        return Path(folder).name + '/' + str(file)

    @staticmethod
    def normalize_name(name):
        if not isinstance(name, str):
            raise ValueError('Enter a campaign name.')
        name = ' '.join(name.split())
        if not name or len(name) > 80:
            raise ValueError('Use a campaign name between 1 and 80 characters.')
        return name

    def create(self, name):
        name = self.normalize_name(name)
        with self.lock:
            data = self.snapshot()
            existing = next((c for c in data['campaigns'] if c['name'].casefold() == name.casefold()), None)
            if existing:
                return existing
            campaign = {'id': secrets.token_hex(8), 'name': name}
            data['campaigns'].append(campaign)
            self._write(data)
            return campaign

    def rename(self, campaign_id, name):
        name = self.normalize_name(name)
        with self.lock:
            data = self.snapshot()
            campaign = next((c for c in data['campaigns'] if c['id'] == campaign_id), None)
            if campaign is None:
                raise KeyError('This campaign no longer exists. Refresh and try again.')
            if any(c['id'] != campaign_id and c['name'].casefold() == name.casefold()
                   for c in data['campaigns']):
                raise ValueError('A campaign with this name already exists.')
            campaign['name'] = name
            self._write(data)

    def assign(self, keys, campaign_id):
        with self.lock:
            data = self.snapshot()
            if campaign_id is not None and not any(c['id'] == campaign_id for c in data['campaigns']):
                raise ValueError('This campaign no longer exists. Refresh and try again.')
            for key in keys:
                if campaign_id is None:
                    data['clips'].pop(key, None)
                else:
                    data['clips'][key] = campaign_id
            self._write(data)

    def initialize_shorts(self, job_id, count, clip_indices=None):
        """Inherit the campaign as clips arrive; preserve later manual changes."""
        if count <= 0:
            return
        with self.lock:
            data = self.snapshot()
            category = data['clips'].get(job_id + '/project')
            if not category:
                return
            counts = data.setdefault('initialized_counts', {})
            # Upgrade older records without marking an empty/partial result final.
            previous = counts.get(job_id)
            if previous is None:
                indexes = [int(key.rsplit('/',1)[1]) for key in data['clips']
                           if key.startswith(job_id + '/clip/') and key.rsplit('/',1)[1].isdigit()]
                previous = max(indexes, default=-1) + 1
            if clip_indices is not None:
                # Results can arrive out of order or omit failed renders.
                # Track seen identities, not a count prefix, so a later lower
                # index inherits once and manual unassignment stays respected.
                indices = {index for index in clip_indices if type(index) is int and index >= 0}
                seen_by_job = data.setdefault('initialized_clip_indices', {})
                seen = set(seen_by_job.get(job_id, range(previous)))
                if job_id in seen_by_job and indices <= seen:
                    return
                for index in indices - seen:
                    key = job_id + '/clip/' + str(index)
                    if key not in data.get('deleted_clips', []):
                        data['clips'].setdefault(key, category)
                seen_by_job[job_id] = sorted(seen | indices)
                counts[job_id] = max(previous, max(indices, default=-1) + 1)
                initialized = data.setdefault('initialized_jobs', [])
                if job_id not in initialized:
                    initialized.append(job_id)
                self._write(data)
                return
            if count <= previous:
                return
            for index in range(previous, count):
                key = job_id + '/clip/' + str(index)
                if key not in data.get('deleted_clips', []):
                    data['clips'].setdefault(key, category)
            counts[job_id] = count
            initialized = data.setdefault('initialized_jobs', [])
            if job_id not in initialized:
                initialized.append(job_id)
            self._write(data)

    def remove(self, campaign_id, delete_videos=False):
        with self.lock:
            data = self.snapshot()
            if not any(c['id'] == campaign_id for c in data['campaigns']):
                raise ValueError('This campaign no longer exists. Refresh and try again.')
            if delete_videos:
                removed = [key for key, value in data['clips'].items() if value == campaign_id]
                data['deleted_clips'] = sorted(set(data.get('deleted_clips', [])) | set(removed))
            data['campaigns'] = [c for c in data['campaigns'] if c['id'] != campaign_id]
            data['clips'] = {key: value for key, value in data['clips'].items() if value != campaign_id}
            self._write(data)

    @staticmethod
    def membership(data, key):
        campaign_id = data['clips'].get(key)
        return next((c for c in data['campaigns'] if c['id'] == campaign_id), None)
