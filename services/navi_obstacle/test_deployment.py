import unittest
from unittest.mock import patch
import deploy_indoor


class DeploymentTest(unittest.TestCase):
    def test_zombie_or_missing_process_counts_as_exited(self):
        with patch('deploy_indoor.Path') as path:
            path.return_value.read_text.return_value = 'Name:\tpython\nState:\tZ (zombie)\n'
            self.assertTrue(deploy_indoor.process_exited(123))
            path.return_value.read_text.return_value = 'Name:\tpython\nState:\tS (sleeping)\n'
            self.assertFalse(deploy_indoor.process_exited(123))
            path.return_value.read_text.side_effect = FileNotFoundError
            self.assertTrue(deploy_indoor.process_exited(123))

    def test_unrelated_process_is_never_stopped(self):
        with patch('deploy_indoor.healthy', return_value={'pid': 123}), \
             patch('deploy_indoor.Path') as path, patch('deploy_indoor.os.kill') as kill:
            path.return_value.read_bytes.return_value = b'python\0/home/other/service.py\0'
            with self.assertRaisesRegex(RuntimeError, 'unrelated process'):
                deploy_indoor.stop_service()
            kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
