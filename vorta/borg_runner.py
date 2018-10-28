import json
import os
import sys
import shutil
import tempfile
import platform
from datetime import datetime as dt
from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication
import subprocess
from subprocess import Popen, PIPE

from .models import SourceDirModel, BackupProfileModel


class BorgThread(QtCore.QThread):
    updated = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object)

    def __init__(self, parent, cmd, params):
        super().__init__(parent)

        # Find packaged borg binary. Prefer globally installed.
        if not shutil.which('borg'):
            meipass_borg = os.path.join(sys._MEIPASS, 'bin', 'borg')
            if os.path.isfile(meipass_borg):
                cmd[0] = meipass_borg
        self.cmd = cmd

        print(cmd)

        env = os.environ.copy()
        env['BORG_HOSTNAME_IS_UNIQUE'] = '1'
        if params.get('password') and params['password']:
            env['BORG_PASSPHRASE'] = params['password']

        if params.get('ssh_key') and params['ssh_key']:
            env['BORG_RSH'] = f'ssh -i ~/.ssh/{params["ssh_key"]}'

        self.env = env
        self.params = params

    def run(self):
        with Popen(self.cmd, stdout=PIPE, stderr=PIPE, bufsize=1, universal_newlines=True, env=self.env) as p:
            for line in p.stderr:
                print(line)
                try:
                    parsed = json.loads(line)
                    if parsed['type'] == 'log_message':
                        self.updated.emit(f'{parsed["levelname"]}: {parsed["message"]}')
                    elif parsed['type'] == 'file_status':
                        self.updated.emit(f'{parsed["path"]} ({parsed["status"]})')
                except json.decoder.JSONDecodeError:
                    self.updated.emit(line.strip())

            p.wait()
            stdout = p.stdout.read()
            result = {
                'params': self.params,
                'returncode': p.returncode,
            }
            try:
                result['data'] = json.loads(stdout)
            except:
                result['data'] = {}

            self.result.emit(result)

    @classmethod
    def create_thread_factory(cls):
        """`borg create` is called from different places and need preparation.
        Centralize it here and return a thread to the caller.
        """
        ret = {
            'ok': False,
        }
        profile = BackupProfileModel.get(id=1)
        app = QApplication.instance()
        n_backup_folders = SourceDirModel.select().count()

        if app.thread and app.thread.isRunning():
            ret['message'] = 'Backup is already in progress.'
            return ret

        if n_backup_folders == 0:
            ret['message'] = 'Add some folders to back up first.'
            return ret

        if profile.repo is None:
            ret['message'] = 'Add a remote backup repository first.'
            return ret

        cmd = ['borg', 'create', '--list', '--info', '--log-json', '--json', '-C', profile.compression]

        # Add excludes
        # Inspired by borgmatic/borgmatic/borg/create.py
        exclude_dirs = []
        for p in profile.exclude_patterns.split('\n'):
            if p.strip():
                expanded_directory = os.path.expanduser(p.strip())
                exclude_dirs.append(expanded_directory)

        if exclude_dirs:
            pattern_file = tempfile.NamedTemporaryFile('w')
            pattern_file.write('\n'.join(exclude_dirs))
            pattern_file.flush()
            cmd.extend(['--exclude-from', pattern_file.name])

        for f in profile.exclude_if_present.split('\n'):
            if f.strip():
                cmd.extend(['--exclude-if-present', f.strip()])

        # Add repo url and source dirs.
        cmd.append(f'{profile.repo.url}::{platform.node()}-{dt.now().isoformat()}')

        for f in SourceDirModel.select():
            cmd.append(f.dir)

        params = {'password': profile.repo.password,
                  'pattern_file': pattern_file}

        app.thread = cls(app, cmd, params)
        ret['message'] = 'Starting Backup.'
        ret['ok'] = True
        ret['thread'] = app.thread

        return ret


