"""Print fingerprints for the reviewed nonlethal area program, without tests.

Run only after reviewing changed owners and their independence assumptions.
Install the emitted data and digest together; stale or edited code must reject
the specialization. This does not update the older joint-expectation contracts.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from wujiang.tactical.rooms import ai
from wujiang.tactical.rooms import equipment_effects, synergy_effects
from wujiang.tactical.heroes.mechanics import electronic_barrier, electronic_teleport, jade
from wujiang.tactical.heroes.mechanics import alexander
from wujiang.tactical.settlement_reuse import contract_functions
from wujiang.tactical.prediction_inline import code_hash

codes = {}
for function in contract_functions():
    key = function.__module__ + ':' + function.__qualname__
    codes.setdefault(key,set()).add(code_hash(function.__code__))
data = json.dumps({'version':1,'codes':{key:sorted(values) for key,values in codes.items()}},sort_keys=True,separators=(',',':'))+'\n'
print(json.dumps({'sha256':hashlib.sha256(data.encode()).hexdigest(),'data':data,'functions':len(codes)}))
