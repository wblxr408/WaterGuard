"""Atomic publication on the same filesystem; readers never observe half-written JSON."""
import json
import os
from pathlib import Path
import tempfile
import time


def write_bytes(path, content):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.'+path.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary,path)
                break
            except PermissionError as exc:
                # Windows readers/indexers can briefly deny replacement. Keep atomic
                # publication and bounded retries; persistent permission failures escape.
                if getattr(exc,'winerror',None) not in (5,32,33) or attempt==5:raise
                time.sleep(.01*2**attempt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write(path,value):
    write_bytes(path,(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8'))
