"""The editor has reloadable routes and safe back navigation."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_editor_routes_and_navigation():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node not installed')
    module = Path(__file__).resolve().parents[1] / 'dashboard/src/lib/editorRoute.js'
    script = """
      import assert from 'node:assert/strict';
      const {parseEditorRoute,editorHash,openEditorRoute,closeEditorRoute} = await import(process.argv[1]);
      assert.deepEqual(parseEditorRoute('#edit/job-123/2'), {targetId:'job-123',clipIndex:2,inputFilename:null});
      const fileId = 'a'.repeat(64);
      const hash = editorHash('file:'+fileId,0,'ai_123.mp4');
      assert.deepEqual(parseEditorRoute(hash), {targetId:'file:'+fileId,clipIndex:0,inputFilename:'ai_123.mp4'});
      assert.equal(parseEditorRoute('#edit/job/0?input_filename=..%2Fclip.mp4').inputFilename,null);
      for (const hash of ['#app','#edit/../0','#edit/%2Fetc/0','#edit/%ZZ/0','#edit/job/-1','#edit/job/9007199254740992','#edit/file:bad/0']) {
        assert.equal(parseEditorRoute(hash),null);
      }
      assert.equal(editorHash('../job',0),null);
      assert.equal(editorHash('job',-1),null);
      assert.equal(editorHash(undefined,0),null);
      assert.equal(editorHash(null,0),null);
      let paused=0, events=0, backed=0, pushes=0, replacements=0;
      globalThis.document={querySelectorAll:()=>[{pause(){paused++;}}]};
      globalThis.window={location:{hash:'#app',pathname:'/',search:''},dispatchEvent(){events++;},history:{
        state:null,length:2,
        pushState(state,_unused,url){this.state=state;window.location.hash=url;pushes++;},
        replaceState(state,_unused,url){this.state=state;window.location.hash=url.slice(url.indexOf('#'));replacements++;},
        back(){backed++;}
      }};
      openEditorRoute('job-123',2);
      assert.equal(window.location.hash,'#edit/job-123/2');
      assert.equal(paused,1); assert.equal(pushes,1); assert.equal(events,1);
      openEditorRoute('job-123',3);
      assert.equal(replacements,1); assert.equal(pushes,1);
      closeEditorRoute(); assert.equal(backed,1);
      window.history.state=null;
      closeEditorRoute(); assert.equal(window.location.hash,'#app');
      assert.equal(backed,1); assert.equal(events,3);
    """
    subprocess.run([node, '--input-type=module', '-e', script, module.as_uri()],
                   check=True, capture_output=True, timeout=30)
