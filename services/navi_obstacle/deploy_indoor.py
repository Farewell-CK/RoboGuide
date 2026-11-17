"""Switch a validated loopback candidate; preserve source and roll back on failure.
Run from a staging copy, after tests and actual CUDA inference on port 18003.
"""
import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import requests
from check_model import verify

ROOT = Path('/home/seaway/sdb/niulinqiang/roboguide_demo')
WORK = ROOT / 'navi-obstacle'
STAGE = Path(__file__).resolve().parent
FILES = ['obstacle_policy.py', 'obstacle_server.py', 'manage_obstacle.sh',
         'model-indoor-manifest.json', 'check_model.py', 'test_obstacle_policy.py',
         'verify_indoor.py', 'eval-room.jpg', 'eval-chairs.jpg', 'eval-sofa.jpg']


def healthy(port, indoor=False):
    response = requests.get(f'http://127.0.0.1:{port}/health', timeout=3)
    response.raise_for_status()
    result = response.json()
    if result['status'] != 'healthy' or (indoor and
            (result.get('profile') != 'indoor' or result.get('policy_version') != 2)):
        raise RuntimeError('Unexpected service health')
    return result


def process_exited(pid):
    try:
        return any(line.startswith('State:') and 'Z' in line
                   for line in Path(f'/proc/{pid}/status').read_text().splitlines())
    except FileNotFoundError:
        return True


def stop_service():
    health = healthy(8003)
    pid = int(health['pid'])
    command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    if str(WORK / 'obstacle_server.py').encode() not in command:
        raise RuntimeError('Refusing to stop an unrelated process')
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if process_exited(pid):
            return
        time.sleep(.1)
    raise RuntimeError('Obstacle process did not exit')


def start_and_check(indoor=False):
    subprocess.run(['bash', str(WORK / 'manage_obstacle.sh')], check=True)
    for _ in range(240):
        try:
            return healthy(8003, indoor)
        except (requests.RequestException, RuntimeError):
            time.sleep(.5)
    raise RuntimeError('Obstacle service failed to become healthy')


def main():
    if STAGE == WORK or STAGE.parent != ROOT:
        raise RuntimeError('Unexpected deployment directory')
    verify(STAGE)
    healthy(18003, True)
    backup = WORK / 'deploy-backups' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-indoor')
    backup.mkdir(parents=True, exist_ok=False)
    old = {name: (WORK / name).exists() for name in FILES}
    for name, exists in old.items():
        if exists:
            shutil.copy2(WORK / name, backup / name)
    (backup / 'previous-health.json').write_text(json.dumps(healthy(8003), indent=2))
    model_target = WORK / 'model-indoor'
    if model_target.exists() or model_target.is_symlink():
        raise RuntimeError('Production indoor model already exists; inspect before replacing')
    guard = WORK / '.deploying'
    guard.touch(exist_ok=False)
    switched = False
    try:
        # The guard-aware manager must be in place before stopping production.
        shutil.copy2(STAGE / 'manage_obstacle.sh', WORK / 'manage_obstacle.sh')
        stop_service()
        switched = True
        for name in FILES:
            shutil.copy2(STAGE / name, WORK / name)
        model_target.symlink_to(STAGE / 'model-indoor', target_is_directory=True)
        guard.unlink()
        health = start_and_check(True)
        with (STAGE / 'eval-room.jpg').open('rb') as sample:
            response = requests.post('http://127.0.0.1:8003/analyze',
                files={'file': ('sample.jpg', sample, 'image/jpeg')},
                data={'frame_id': 'production-smoke'}, timeout=10)
        response.raise_for_status()
        if response.json().get('profile') != 'indoor':
            raise RuntimeError('Unexpected inference result')
        print(json.dumps({'deployed': health, 'backup': str(backup)}, ensure_ascii=False))
    except BaseException:
        if switched:
            try:
                stop_service()
            except requests.RequestException:
                # A failed startup may not have a health endpoint yet.
                pid_path = WORK / 'service.pid'
                if pid_path.exists():
                    pid = int(pid_path.read_text())
                    path = Path(f'/proc/{pid}/cmdline')
                    if path.exists() and str(WORK / 'obstacle_server.py').encode() in path.read_bytes().split(b'\0'):
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(1)
        for name, exists in old.items():
            if exists:
                shutil.copy2(backup / name, WORK / name)
            elif (WORK / name).exists():
                (WORK / name).unlink()
        if model_target.is_symlink():
            model_target.unlink()
        guard.unlink(missing_ok=True)
        start_and_check()
        raise
    finally:
        guard.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
