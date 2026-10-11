import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_eval import acquire_run_lock

HOLDER = """
import sys
sys.path.insert(0, {eval_dir!r})
from pathlib import Path
from run_eval import acquire_run_lock
acquire_run_lock(Path({lock!r}))
print("locked", flush=True)
sys.stdin.read()
"""


class RunLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lock = Path(tmp.name) / ".run_eval.lock"

    def _spawn_holder(self) -> subprocess.Popen:
        code = HOLDER.format(eval_dir=str(Path(__file__).resolve().parent), lock=str(self.lock))
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._stop, proc)
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        return proc

    @staticmethod
    def _stop(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdin.close()
        proc.stdout.close()

    def test_second_run_refused_while_first_alive(self):
        holder = self._spawn_holder()
        with self.assertRaises(SystemExit) as ctx:
            acquire_run_lock(self.lock)
        self.assertIn(f"pid {holder.pid}", str(ctx.exception))

    def test_lock_freed_when_holder_dies(self):
        holder = self._spawn_holder()
        self._stop(holder)
        fd = acquire_run_lock(self.lock)
        os.close(fd)

    def test_stale_lock_file_does_not_block(self):
        self.lock.write_text(" 99999          ", encoding="ascii")
        fd = acquire_run_lock(self.lock)
        os.close(fd)


if __name__ == "__main__":
    unittest.main()
