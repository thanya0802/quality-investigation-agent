import json
import sys
import time
import uuid

def new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def log(**fields):
    fields.setdefault("ts", now())
    print(json.dumps(fields, default=str), file=sys.stderr, flush=True)
