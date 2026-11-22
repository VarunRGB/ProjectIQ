"""Sealed-template MySQL check must remain unprivileged."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import template_bake


class SealedMysqlProbeTests(unittest.TestCase):
    def test_available_without_sudo(self):
        class SSH:
            def __init__(self):
                self.calls = []
            def ssh(self, *args, **kwargs):
                self.calls.append(args)
                return SimpleNamespace(returncode=0)
        ssh = SSH()
        template_bake._wait_mysql_available_without_sudo(ssh, seconds=2)
        self.assertEqual(len(ssh.calls), 2)
        self.assertEqual(ssh.calls[0][:3], ('systemctl', 'is-active', '--quiet'))
        self.assertEqual(ssh.calls[1][0], '/usr/bin/mysqladmin')
        self.assertIn('ping', ssh.calls[1])
        self.assertFalse(any('sudo' in item for call in ssh.calls for item in call))

    def test_inactive_or_unreachable_is_denied(self):
        class SSH:
            def __init__(self, service_code, ping_code):
                self.service_code, self.ping_code = service_code, ping_code
                self.calls = []
            def ssh(self, *args, **kwargs):
                self.calls.append(args)
                return SimpleNamespace(returncode=(self.service_code if args[0] == 'systemctl'
                                                   else self.ping_code))
        for active, responding in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(service_code=active, ping_code=responding):
                ssh = SSH(active, responding)
                with patch.object(template_bake.time, 'monotonic', side_effect=[0, 99]):
                    with self.assertRaises(template_bake.WorkerFailure):
                        template_bake._wait_mysql_available_without_sudo(ssh, seconds=2)
                self.assertFalse(any('sudo' in item for call in ssh.calls for item in call))


if __name__ == '__main__':
    unittest.main()
