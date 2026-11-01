import sqlite3
import tempfile
import unittest
from pathlib import Path

from opsgraph.agentsight import load_sqlite


class AgentSightSQLiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "agentsight-demo.db"
        conn = sqlite3.connect(cls.path)
        conn.executescript(
            """
            CREATE TABLE llm_calls (
              id TEXT PRIMARY KEY, session_id TEXT, start_timestamp_ms INTEGER,
              end_timestamp_ms INTEGER, pid INTEGER, comm TEXT, provider TEXT,
              model TEXT, status TEXT, view_source TEXT
            );
            CREATE TABLE token_usage (
              id TEXT PRIMARY KEY, llm_call_id TEXT, timestamp_ms INTEGER,
              model TEXT, provider TEXT, input_tokens INTEGER, output_tokens INTEGER,
              total_tokens INTEGER
            );
            CREATE TABLE tool_calls (
              id TEXT PRIMARY KEY, session_id TEXT, timestamp_ms INTEGER,
              tool_name TEXT, status TEXT, duration_ms INTEGER, input_json TEXT,
              output_json TEXT, related_pid INTEGER, view_source TEXT
            );
            CREATE TABLE audit_events (
              id TEXT PRIMARY KEY, timestamp_ms INTEGER, audit_type TEXT, pid INTEGER,
              comm TEXT, subject TEXT, action TEXT, target TEXT, status TEXT,
              summary TEXT, details_json TEXT, view_source TEXT
            );
            CREATE TABLE process_nodes (
              id TEXT PRIMARY KEY, pid INTEGER, ppid INTEGER, root_pid INTEGER,
              start_timestamp_ms INTEGER, end_timestamp_ms INTEGER, comm TEXT,
              command TEXT, argv_json TEXT, cwd TEXT, exit_code INTEGER, status TEXT,
              view_source TEXT
            );
            CREATE TABLE network_targets (
              id TEXT PRIMARY KEY, pid INTEGER, comm TEXT, host TEXT, path TEXT,
              count INTEGER, error_count INTEGER, first_timestamp_ms INTEGER,
              last_timestamp_ms INTEGER
            );
            CREATE TABLE resource_samples (
              timestamp_ms INTEGER, pid INTEGER, comm TEXT, cpu_percent REAL, rss_mb INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO llm_calls VALUES ('call-1','sess-1',1000,9000,500,'claude','anthropic','claude-sonnet-4','observed','sqlite')"
        )
        conn.execute(
            "INSERT INTO token_usage VALUES ('tok-1','call-1',1000,'claude-sonnet-4','anthropic',1200,300,1500)"
        )
        conn.execute(
            "INSERT INTO tool_calls VALUES ('tool-1','sess-1',2000,'kubectl_apply','failed',1200,'{\"command\":\"kubectl apply -f x.yaml\"}','{\"error\":\"forbidden\"}',500,'sqlite')"
        )
        conn.execute(
            "INSERT INTO tool_calls VALUES ('tool-2','sess-1',3000,'kubectl_apply','success',900,'{\"command\":\"kubectl apply -f x.yaml\"}','{\"ok\":true}',500,'sqlite')"
        )
        conn.execute(
            "INSERT INTO audit_events VALUES ('audit-1',3000,'process',500,'kubectl','sess-1','exec','kubectl apply -f x.yaml','success','applied','{\"session_id\":\"sess-1\",\"risk\":\"high\"}','sqlite')"
        )
        conn.execute(
            "INSERT INTO process_nodes VALUES ('proc-1',500,1,500,1000,9000,'claude','claude','[]','/workspace','0','success','sqlite')"
        )
        conn.execute(
            "INSERT INTO network_targets VALUES ('net-1',500,'claude','api.anthropic.com','/v1/messages',2,0,1000,9000)"
        )
        conn.execute("INSERT INTO resource_samples VALUES (5000,500,'claude',11.5,280)")
        conn.commit()
        conn.close()
        cls.snapshot = load_sqlite(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sqlite_session_and_tokens(self):
        self.assertEqual(len(self.snapshot.sessions), 1)
        session = self.snapshot.sessions[0]
        self.assertEqual(session.key, "sess-1")
        self.assertEqual(session.total_tokens, 1500)
        self.assertEqual(session.pid, 500)
        self.assertEqual(session.attributes["cwd"], "/workspace")
        self.assertGreater(session.cpu_peak, 10)

    def test_sqlite_tools_and_effects(self):
        self.assertEqual(len(self.snapshot.tool_calls), 2)
        self.assertTrue(self.snapshot.tool_calls[0].failed)
        self.assertTrue(any(item.effect_type == "process" for item in self.snapshot.effects))
        self.assertTrue(any(item.effect_type == "network" for item in self.snapshot.effects))


if __name__ == "__main__":
    unittest.main()
