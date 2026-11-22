"""Phase 4D-C experimental eval-cgroup budget; no actual VM launch."""
from __future__ import annotations
import unittest
from projectiq_executioner.phase2.vm_worker import guest_agent, qemu

class Browser128TaskExperiment(unittest.TestCase):
    def test_runtime_seed_and_guest_verifier_agree(self):
        self.assertEqual(qemu.APP_CGROUP_PIDS, 128)
        self.assertEqual(guest_agent.APP_CGROUP_PIDS, 128)
        self.assertIn('TasksMax=128', qemu.APP_CGROUP_BOOTSTRAP_SCRIPT)
        self.assertNotIn('TasksMax=80', qemu.APP_CGROUP_BOOTSTRAP_SCRIPT)
        self.assertIn("node.joinpath('pids.max').read_text().strip() != str(APP_CGROUP_PIDS)",
                      __import__('inspect').getsource(guest_agent._evaluation_policy_matches))

    def test_existing_memory_io_and_mysql_limits_unchanged(self):
        self.assertEqual(qemu.APP_CGROUP_MEMORY_BYTES, 2147483648)
        self.assertEqual(guest_agent.APP_CGROUP_MEMORY_BYTES, 2147483648)
        self.assertEqual(qemu.APP_CGROUP_IO_BPS, 16777216)
        self.assertEqual(guest_agent.APP_CGROUP_IO_BPS, 16777216)
        bootstrap = qemu.APP_CGROUP_BOOTSTRAP_SCRIPT
        self.assertIn('MemoryMax=2147483648', bootstrap)
        self.assertIn('IOWriteBandwidthMax=/srv/projectiq-workspace 16777216', bootstrap)
        self.assertIn('TasksAccounting=yes', bootstrap)
        self.assertIn("bounded('pids.max', 64)", qemu.MYSQL_BOOTSTRAP_SCRIPT)

    def test_browser_profile_and_isolation_intact(self):
        launcher = guest_agent.APP_LIMIT_LAUNCHER
        self.assertIn('192 if _browser_requested else 96', launcher)
        self.assertIn('resource.RLIMIT_NOFILE, 512', launcher)
        self.assertIn('resource.RLIMIT_CORE, 0', launcher)
        self.assertIn('PROJECTIQ_EVAL_REQUIRED', launcher)
        self.assertIn('projectiq-eval.slice', launcher)
        self.assertIn('os.execvpe(sys.argv[1], sys.argv[1:], os.environ)', launcher)

    def test_per_runtime_cloud_init_generation_not_template_bake(self):
        import inspect
        src = inspect.getsource(qemu.make_nocloud_iso)
        self.assertIn('if app_cgroup:', src)
        self.assertIn("APP_CGROUP_BOOTSTRAP_SCRIPT.encode('utf-8')", src)
        self.assertIn('bootstrap_tasks.append', src)

if __name__ == '__main__':
    unittest.main()
