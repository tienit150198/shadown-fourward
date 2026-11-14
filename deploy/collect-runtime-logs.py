"""Follow the app across container recreations; keep at most ~30 MB on disk."""
import logging
import logging.handlers
import os
from pathlib import Path
import subprocess
import time

os.umask(0o077)
root = Path('/opt/shadow/logs')
root.mkdir(mode=0o700, parents=True, exist_ok=True)
log = logging.getLogger('capture')
log.setLevel(logging.INFO)
log.addHandler(logging.handlers.RotatingFileHandler(
    root / 'runtime.log', maxBytes=5_000_000, backupCount=5, encoding='utf-8'))
since = '10m'
last = ''
while True:
    # docker logs exits when a container stops; reopen by name after recreation.
    with subprocess.Popen(
        ['docker', 'logs', '--follow', '--timestamps', '--since', since, 'shadow-hackathon-app-1'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    ) as process:
        for line in process.stdout:
            line = line.rstrip('\n')
            stamp = line.split(' ', 1)[0]
            if len(stamp) < 20 or not stamp[:4].isdigit() or not stamp.endswith('Z'):
                continue
            if line != last:
                log.info('%s', line)
            since, last = stamp, line
    time.sleep(2)
