import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'nt', 'Windows batch launchers')
class WindowsLauncherTests(unittest.TestCase):
    def test_run_and_setup_forward_switches_and_preserve_exit_codes(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='EIA stats test ') as directory:
            root = Path(directory) / 'Project with spaces'
            (root / 'old_stats').mkdir(parents=True)
            for name in ('RUN_EIA_STATS.bat', 'SETUP_WINDOWS.bat', 'run_eia_stats_task.ps1'):
                shutil.copy2(source / name, root / name)
            (root / 'old_stats' / 'run_eia_stats_task.ps1').write_text(
                'param([switch]$SetupOnly,[switch]$ShowDecision,[double]$IntervalSeconds)\n'
                'if ($SetupOnly) { Write-Host "Setup only"; exit 0 }\n'
                'if (-not $ShowDecision -or $IntervalSeconds -ne 0.5) { exit 99 }\n'
                'Write-Host "Arguments forwarded"\nexit 37\n')
            env = {**os.environ, 'EIA_NO_PAUSE': '1'}
            result = subprocess.run(
                f'cmd.exe /d /s /c ""{root / "RUN_EIA_STATS.bat"}" -ShowDecision -IntervalSeconds 0.5"',
                cwd=directory, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
            self.assertIn('Arguments forwarded', result.stdout)
            setup = subprocess.run(f'cmd.exe /d /s /c ""{root / "SETUP_WINDOWS.bat"}""',
                                   cwd=directory, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            self.assertIn('Setup only', setup.stdout)
