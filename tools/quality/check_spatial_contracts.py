"""Validate actual spatial files through independent pinned JSON Schema in a separate process."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def run(python: Path, point_root: Path, planar_root: Path, output: Path) -> dict[str, Any]:
    """Check actual documents and negative types independently of provider validation."""
    program = r"""import hashlib,importlib.metadata,json,sys
from pathlib import Path
from jsonschema import Draft202012Validator,ValidationError
if importlib.metadata.version("jsonschema")!="4.26.0":raise ValueError("validator identity differs")
root=Path(sys.argv[1]);point=Path(sys.argv[2]);planar=Path(sys.argv[3])
schemas={p.stem.removesuffix(".schema"):json.loads(p.read_bytes())
         for p in root.glob("*.schema.json")}
assert len(schemas)==5
for schema in schemas.values():Draft202012Validator.check_schema(schema)
documents=[]
for directory,kind in ((point,"point-cloud"),(planar,"occupancy")):
 for path in sorted(directory.glob("*-request.json")):
  Draft202012Validator(schemas[kind+"-job"]).validate(json.loads(path.read_bytes()))
  documents.append(str(path))
 for path in sorted(directory.glob("*-result.json")):
  value=json.loads(path.read_bytes())
  if value.get("manifest"):
   Draft202012Validator(schemas[kind+"-map"]).validate(value["manifest"])
   documents.append(str(path))
for path in sorted((planar/"inputs").glob("*.json")):
 Draft202012Validator(schemas["occupancy-grid"]).validate(json.loads(path.read_bytes()))
 documents.append(str(path))
assert len(documents)>=20
base=json.loads((planar/"planar-loop-request.json").read_bytes())
negative=[]
cases=(("boolean-budget",{"policy":{**base["policy"],"timeout_s":True}}),
 ("invented-node-selector",{"node_id":"invented"}),
 ("wrong-dimensional-format",{"inputs":[{**base["inputs"][0],"format":"ply"}]}))
for name,patch in cases:
 try:Draft202012Validator(schemas["occupancy-job"]).validate({**base,**patch})
 except ValidationError:negative.append(name)
 else:raise AssertionError(name)
manifest=json.loads((planar/"planar-loop-result.json").read_bytes())["manifest"]
for name,patch in (("invented-altitude-pose",{"geometry":"SE(3)"}),
                   ("uncalibrated-covariance",{"covariance":[[1,0],[0,1]]})):
 try:Draft202012Validator(schemas["occupancy-map"]).validate({**manifest,**patch})
 except ValidationError:negative.append(name)
 else:raise AssertionError(name)
print(json.dumps({"passed":True,"validator":"jsonschema/4.26.0", "schema_count":len(schemas),
 "schema_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(root.glob("*.schema.json"))},
 "actual_documents":len(documents),"rejected_cases":negative,"documents":documents}))
"""
    completed = subprocess.run(
        [
            str(python),
            "-c",
            program,
            str(ROOT / "contracts/spatial/file-provider-v0.1"),
            str(point_root),
            str(planar_root),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=60,
    )
    if len(completed.stdout) > 1024 * 1024:
        raise ValueError("schema check response exceeded its bound")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or value.get("passed") is not True:
        raise ValueError("schema check did not return complete affirmative software evidence")
    with output.open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")
    return value


def main() -> None:
    """Retain a new independent schema receipt beside the actual private geometry evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--point-root", type=Path, required=True)
    parser.add_argument("--planar-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = run(args.python, args.point_root, args.planar_root, args.output)
    print(json.dumps({key: item for key, item in value.items() if key != "documents"}))


if __name__ == "__main__":
    main()
