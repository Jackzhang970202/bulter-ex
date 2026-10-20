"""经验管家全量测试：验收标准来自《设计与实施计划》§10。
运行：cd butler && python -m unittest discover -s tests -v"""
import json
import unittest
from pathlib import Path

import helpers as H
from butler import config, db, repo, filterpipe, parser, renderer, connector, exporter


class T01_P0(unittest.TestCase):
    def setUp(self):
        H.setup_home()
        filterpipe._compiled, filterpipe._compiled_mtime = None, None

    def test_seed_rules_match(self):
        cases = {
            "AKIAABCDEFGHIJKLMNOP": "aws_key",
            "sk-proj-Abcd1234efgh5678ijkl": "api_key",
            "password:Sup3rSecretXY": "credential_pair",
            "11010119900307123X": "cn_idcard",
            "13800138000": "cn_mobile",
            "someone@example.com": "email",
            "eyJhbGciOiJIUzI1NiIs.rGqW9UFz.testsign": "jwt",
        }
        for raw, cat in cases.items():
            masked, cats = filterpipe.mask_text(f"噪声 {raw} 噪声")
            self.assertIn(cat, cats, f"{raw} 未被拦截")
            self.assertNotIn(raw, masked, f"{raw} 原文泄漏")

    def test_pem_block(self):
        pem = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\nmore\n"
               "-----END RSA PRIVATE KEY-----")
        masked, cats = filterpipe.mask_text(pem)
        self.assertIn("private_key", cats)
        self.assertNotIn("MIIEow", masked)

    def test_bad_pattern_tolerance(self):
        config.FILTER_RULES_PATH.write_text(
            config.FILTER_RULES_PATH.read_text("utf-8") + "([unclosed ||| broken ||| x\n",
            "utf-8")
        filterpipe._compiled = None
        pats = filterpipe.active_patterns()          # 不抛异常
        self.assertTrue(all(p for p in pats))
        self.assertTrue(db.conn().execute(
            "SELECT 1 FROM errors WHERE kind='bad_pattern'").fetchone())

    def test_exporter(self):
        repo.add_candidate("rule", {"mode": "forbid", "statement": "测试规则",
                                    "ex_bad": "错", "ex_good": "对"})
        cid = db.conn().execute("SELECT MAX(id) m FROM candidates").fetchone()["m"]
        repo.decide_candidate(cid, "approve")
        out = exporter.export_kit()
        self.assertIn("测试规则", (out / "RULES.md").read_text("utf-8"))
        self.assertTrue((out / "MANIFEST.md").exists())


class T02_P1_parser(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(H.setup_home())

    def test_usage_parse(self):
        d = parser.parse_line(H.asst_msg("a1", "hi"))
        self.assertEqual(d["usage"]["cache_r"], 1000)
        self.assertEqual(d["usage"]["out"], 50)

    def test_half_line_and_crlf(self):
        p = self.tmp / "s.jsonl"
        content = (H.user_msg("u1", "第一行") + "\r\n" + H.asst_msg("a1", "第二行")
                   + "\n" + H.user_msg("u9", "半行未写完"))   # 无换行结尾
        p.write_bytes(content.encode("utf-8"))
        e, off, unk, _, nxt = parser.read_increment(p, 0, 1)
        self.assertEqual(len(e), 2)
        self.assertEqual(nxt, 3)                             # 下一物理行号
        e2, off2, _, _, nxt2 = parser.read_increment(p, off, 3)
        self.assertEqual((len(e2), off2), (0, off))          # 半行不消费
        p.write_bytes((content + "\n").encode("utf-8"))      # 补完
        e3, off3, _, _, _ = parser.read_increment(p, off, 3)
        self.assertEqual(len(e3), 1)
        self.assertEqual(e3[0]["line_no"], 3)                # 行号连续
        self.assertEqual(e[1]["line_no"], 2)                 # CRLF 行号正确

    def test_blank_lines_counted_in_line_numbers(self):
        # 空行计入物理行号（next_line 透出），证据回放不再错位
        p = self.tmp / "s3.jsonl"
        p.write_text(H.user_msg("u1", "第一条") + "\n\n\n" + H.asst_msg("a1", "第二条")
                     + "\n", "utf-8")
        e, off, unk, _, nxt = parser.read_increment(p, 0, 1)
        self.assertEqual([d["line_no"] for d in e], [1, 4])
        self.assertEqual(nxt, 5)

    def test_u2028_does_not_split_line(self):
        #  在 JSON 字符串值里合法：不得被当成行界（假漂移防线）
        p = self.tmp / "s4.jsonl"
        msg = json.dumps({"type": "user", "uuid": "u1", "timestamp": "x",
                          "message": {"role": "user", "content":
                                      [{"type": "text", "text": "前 后"}]}},
                         ensure_ascii=False)
        p.write_bytes((msg + "\n").encode("utf-8"))
        e, off, unk, _, _ = parser.read_increment(p, 0, 1)
        self.assertEqual((unk, len(e)), (0, 1))

    def test_unknown_tolerated(self):
        p = self.tmp / "s2.jsonl"
        p.write_text('{"totally":"unknown"}\n' + H.user_msg("u1", "ok") + "\n", "utf-8")
        e, off, unk, samples, _ = parser.read_increment(p, 0, 1)
        self.assertEqual((unk, len(e)), (1, 1))
        self.assertTrue(samples)

    def test_compact_boundary(self):
        d = parser.parse_line(json.dumps({"type": "system", "subtype": "compact_boundary",
                                          "uuid": "c1", "compactMetadata": {}}))
        self.assertTrue(d["compact"])


class T03_P1_collector(unittest.TestCase):
    def setUp(self):
        self.tmp = H.setup_home()
        slug = config.CLAUDE_PROJECTS / "D--proj-demo"
        slug.mkdir(parents=True)
        self.sp = slug / "sess-1.jsonl"
        lines = [H.user_msg("u1", "别加括号（解释）"),
                 H.asst_msg("a1", "收到"),
                 json.dumps({"type": "system", "subtype": "compact_boundary", "uuid": "c1"}),
                 H.user_msg("u2", "继续干活"),
                 H.asst_msg("a2", "好")]
        self.sp.write_text("\n".join(lines) + "\n", "utf-8")
        from butler.collector import Collector
        self.col = Collector()

    def test_sweep(self):
        self.col.sweep_once()
        row = db.conn().execute("SELECT * FROM sessions WHERE session_id='sess-1'").fetchone()
        self.assertTrue(row and row["file_offset"] == self.sp.stat().st_size)
        self.assertEqual(row["project_path"], "D:\\proj-demo")
        u = db.conn().execute("SELECT SUM(tokens) t FROM usage_daily").fetchone()["t"]
        self.assertEqual(u, (100 + 1000 + 50) * 2)                   # 两条 assistant
        self.assertTrue(db.conn().execute(
            "SELECT 1 FROM events WHERE kind='compact'").fetchone())
        # 待确认项目登记
        self.assertTrue(any(p["path"] == "D:\\proj-demo"
                            for p in repo.pending_projects()))

    def test_incremental_and_drift(self):
        self.col.sweep_once()
        off = db.conn().execute("SELECT file_offset FROM sessions").fetchone()["file_offset"]
        self.sp.write_text(self.sp.read_text("utf-8") + H.user_msg("u9", "新的一轮") + "\n",
                           "utf-8")
        self.col.sweep_once()
        off2 = db.conn().execute("SELECT file_offset FROM sessions").fetchone()["file_offset"]
        self.assertGreater(off2, off)
        # 漂移：灌 220 行未知 → 触发 schema_drift + 样本
        for i in range(220):
            self.sp.write_text(self.sp.read_text("utf-8") + json.dumps({"x": i}) + "\n",
                               "utf-8")
        self.col.sweep_once()
        self.assertTrue(db.conn().execute(
            "SELECT 1 FROM errors WHERE kind='schema_drift'").fetchone())
        self.assertTrue(list(config.SAMPLES.glob("drift-*.txt")))

    def test_usage_bucketed_by_message_ts(self):
        hist = json.dumps({"type": "assistant", "uuid": "old1",
                           "timestamp": "2020-01-01T09:00:00.000Z", "cwd": "D:\\proj-demo",
                           "message": {"role": "assistant", "content":
                                       [{"type": "text", "text": "旧"}],
                                       "usage": {"input_tokens": 10,
                                                 "cache_creation_input_tokens": 0,
                                                 "cache_read_input_tokens": 0,
                                                 "output_tokens": 5}}})
        self.sp.write_text(self.sp.read_text("utf-8") + hist + "\n", "utf-8")
        self.col.sweep_once()
        row = db.conn().execute(
            "SELECT * FROM usage_daily WHERE day='2020-01-01'").fetchone()
        self.assertTrue(row)                              # 归入消息当天而非今天
        self.assertEqual(row["tokens"], 15)

    def test_inbox_note_to_candidate(self):
        (config.INBOX / "note-ab12.md").write_text(
            "kind: rule\nmode: forbid\nstatement: 测试条目\nex_bad: 错\n"
            "ex_good: 对\ndedup: new\nconfidence: high\nevidence: 用户说过\n", "utf-8")
        self.col.sweep_inbox()
        cs = repo.candidates_list(True)
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["payload"]["statement"], "测试条目")
        self.assertTrue(list(config.INBOX.glob("*.done")))


class T04_P2_renderer_connector(unittest.TestCase):
    def setUp(self):
        self.tmp = H.setup_home()

    def test_render_contains_rules_and_protocol(self):
        cid = repo.add_candidate("rule", {"mode": "forbid", "statement": "禁止括号解释",
                                          "ex_bad": "性能（速度）", "ex_good": "性能"})
        repo.decide_candidate(cid, "approve")
        prj = self.tmp / "projX"
        prj.mkdir()
        connector.install(str(prj))
        r = renderer.render_all()
        h = config.project_hash(str(prj))
        digest = (config.RELEASES / f"{h}.md").read_text("utf-8")
        self.assertIn("禁止括号解释", digest)
        self.assertIn("记入规则", digest)              # 协议区
        self.assertIn("重新读发行版", digest)
        self.assertIn("当前用户指令 > 本发行版", digest)
        hook = config.HOOK_BAT.read_text("utf-8")
        self.assertIn("%~dp0", hook)                  # 自定位，搬家不断
        self.assertIn("exit /b 0", hook)

    def test_size_guard(self):
        s = config.load_settings(); s["digest_max_bytes"] = 50
        config.save_settings(s)
        for i in range(6):
            repo.add_candidate("rule", {"mode": "forbid", "statement": f"长规则{i}" * 8,
                                         "ex_bad": "a", "ex_good": "b"})
        prj = self.tmp / "projY"; prj.mkdir()
        connector.install(str(prj))
        renderer.render_all()
        # 超限→拒写项目发行版（宁用旧版），并记错误
        h = config.project_hash(str(prj))
        self.assertTrue(db.conn().execute(
            "SELECT 1 FROM errors WHERE kind='digest_over_limit'").fetchone())

    def test_settings_merge_and_uninstall(self):
        prj = self.tmp / "projZ"; (prj / ".claude").mkdir(parents=True)
        (prj / ".claude" / "settings.local.json").write_text(json.dumps(
            {"permissions": {"allow": ["Bash"]},
             "hooks": {"SessionStart": [{"matcher": "startup",
                                         "hooks": [{"type": "command",
                                                   "command": "echo user-own"}]}]}}),
            "utf-8")
        connector.install(str(prj))
        data = json.loads((prj / ".claude" / "settings.local.json").read_text("utf-8"))
        self.assertEqual(data["permissions"]["allow"], ["Bash"])      # 原样保留
        ss = data["hooks"]["SessionStart"]
        self.assertEqual(len(ss), 2)
        connector.install(str(prj))                                    # 幂等
        self.assertEqual(len(json.loads((prj / ".claude" / "settings.local.json")
                                       .read_text("utf-8"))["hooks"]["SessionStart"]), 2)
        connector.uninstall(str(prj))
        data = json.loads((prj / ".claude" / "settings.local.json").read_text("utf-8"))
        self.assertEqual(len(data["hooks"]["SessionStart"]), 1)       # 只摘我方
        self.assertEqual(data["hooks"]["SessionStart"][0]["hooks"][0]["command"],
                         "echo user-own")


class T05_P3_distiller(unittest.TestCase):
    def setUp(self):
        self.tmp = H.setup_home()
        slug = config.CLAUDE_PROJECTS / "D--p"
        slug.mkdir()
        p = slug / "sX.jsonl"
        p.write_text("\n".join([
            H.user_msg("u1", "key AKIAABCDEFGHIJKLMNOP 别外传"),
            H.asst_msg("a1", "好的，已注意"),
            H.user_msg("u2", "以后都别用括号补充解释"),
            H.asst_msg("a2", "明白"),
        ]) + "\n", "utf-8")
        from butler.collector import Collector
        Collector().sweep_once()                                      # 置水位

    def _fake_claude(self, prompt, cwd, timeout=1200):
        # 断言：进入模型的 prompt 绝无原文密钥
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", prompt)
        self.assertIn("[REDACTED:aws_key]", prompt)
        return {"result": '''分析完毕。```json
{"candidates":[{"kind":"rule","mode":"forbid","statement":"禁止括号补充解释","ex_bad":"性能（速度）","ex_good":"性能","dedup":"new","confidence":"high","refs":[2]}],
"metrics":{"summary":"一条协作规范"}}```'''.replace('"refs":[2]', '"refs":[2]'),
            "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01}

    def test_run_full(self):
        from butler import distiller as D
        D._call_claude = self._fake_claude
        D._run()
        run = db.conn().execute("SELECT * FROM distill_runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(run["status"], "ok")
        cs = repo.candidates_list(True)
        self.assertEqual(len(cs), 1)
        ev = cs[0]["evidence"][0]
        self.assertEqual(ev["session"], "sX")
        self.assertEqual(ev["line"], 3)
        # 水位推进到文件尾
        row = db.conn().execute("SELECT * FROM sessions WHERE session_id='sX'").fetchone()
        self.assertGreaterEqual(row["consumed_offset"], row["file_offset"] - 2)
        # 审计只有元数据
        self.assertTrue(db.conn().execute(
            "SELECT 1 FROM redact_audit WHERE category='aws_key'").fetchone())
        a = db.conn().execute("SELECT * FROM redact_audit LIMIT 1").fetchone()
        self.assertNotIn("AKIA", json.dumps(dict(a)))

    def test_budget_rollover(self):
        from butler import distiller as D
        s = config.load_settings(); s["distill_max_input_chars"] = 60
        config.save_settings(s)
        calls = []

        def fake(prompt, cwd, timeout=1200):
            calls.append(prompt)
            return {"result": '{"candidates":[],"metrics":{}}',
                    "usage": {"output_tokens": 1}}
        D._call_claude = fake
        D._run()
        run = db.conn().execute("SELECT * FROM distill_runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(run["status"], "ok")
        self.assertGreater(run["rolled_chars"], 0)                    # 有滚存
        row = db.conn().execute("SELECT * FROM sessions WHERE session_id='sX'").fetchone()
        self.assertLess(row["consumed_offset"], row["file_offset"])   # 未消费部分留着

    def test_violation_repeat_bumps_rule(self):
        from butler import distiller as D
        cid = repo.add_candidate("rule", {"mode": "forbid", "statement": "旧条文",
                                          "ex_bad": "a", "ex_good": "b"})
        repo.decide_candidate(cid, "approve")
        D._call_claude = lambda p, c, timeout=1200: {"result": '```json\n' + json.dumps({
            "candidates": [{"kind": "rule", "dedup": "violation_repeat", "ref": "T1",
                            "statement": "具体化条文", "ex_bad": "x", "ex_good": "y",
                            "refs": [0]}], "metrics": {}}, ensure_ascii=False) + "\n```",
            "usage": {}}
        D._run()
        c = repo.candidates_list(True)[0]
        self.assertEqual(c["dedup_ref"], "T1")
        self.assertEqual(db.conn().execute(
            "SELECT corrections FROM rules WHERE id='T1'").fetchone()["corrections"], 1)
        # 批准替换 → 条文被改写
        repo.decide_candidate(c["id"], "approve")
        self.assertEqual(db.conn().execute(
            "SELECT statement FROM rules WHERE id='T1'").fetchone()["statement"], "具体化条文")


class T06_E2E_promote_rerender_server(unittest.TestCase):
    def setUp(self):
        self.tmp = H.setup_home()

    def test_approve_triggers_digest_update(self):
        prj = self.tmp / "projE"; prj.mkdir()
        connector.install(str(prj))
        renderer.render_all()
        cid = repo.add_candidate("rule", {"mode": "require", "statement": "不确定先问",
                                          "ex_bad": "猜", "ex_good": "问"})
        from butler import server
        server.decide({"id": cid, "action": "approve"})
        h = config.project_hash(str(prj))
        self.assertIn("不确定先问", (config.RELEASES / f"{h}.md").read_text("utf-8"))

    def test_empty_statement_rejected_at_ingest(self):
        # 语义更新：空 statement 的候选在 add_candidate 即被拒（返回 0，不入库）
        cid = repo.add_candidate("rule", {"mode": "forbid", "statement": ""})
        self.assertEqual(cid, 0)
        self.assertEqual(len(repo.candidates_list(True)), 0)
        # 有 statement 无例子：distiller 层已拦（T05 语义）；repo 层允许直提
        cid2 = repo.add_candidate("rule", {"mode": "forbid", "statement": "有陈述"})
        self.assertGreater(cid2, 0)


class T07_Dedup(unittest.TestCase):
    """机械去重：主通道=全量自动蒸馏，不依赖口令；同义候选合并/静默，拒绝过不再骚扰。"""

    def setUp(self):
        H.setup_home()

    def _rule(self, stmt="禁止括号解释"):
        return {"mode": "forbid", "statement": stmt, "ex_bad": "性能（速度）", "ex_good": "性能"}

    def test_pending_merge(self):
        i1 = repo.add_candidate("rule", self._rule(), origin="d1",
                                evidence=[{"session": "s", "line": 1}])
        i2 = repo.add_candidate("rule", self._rule(), origin="d2",
                                evidence=[{"session": "s", "line": 2},
                                          {"session": "s", "line": 1}])
        self.assertEqual(i1, i2)
        c = repo.candidates_list(True)[0]
        self.assertEqual(c["times_seen"], 2)
        self.assertEqual(len(c["evidence"]), 2)            # 重复证据去重
        self.assertIn("d2", c["origin"])

    def test_approved_then_repeat_becomes_rewrite_proposal(self):
        i1 = repo.add_candidate("rule", self._rule(), origin="d1")
        repo.decide_candidate(i1, "approve")
        cid = db.conn().execute("SELECT id FROM rules WHERE statement LIKE '%括号%'").fetchone()["id"]
        i2 = repo.add_candidate("rule", self._rule(), origin="d2")   # 再次出现
        self.assertGreater(i2, 0)
        c = repo.candidates_list(True)[0]
        self.assertEqual(c["dedup"], "violation_repeat")
        self.assertEqual(c["dedup_ref"], cid)
        self.assertEqual(db.conn().execute(
            "SELECT corrections FROM rules WHERE id=?", (cid,)).fetchone()["corrections"], 1)

    def test_rejected_is_silenced(self):
        i1 = repo.add_candidate("rule", self._rule(), origin="d1")
        repo.decide_candidate(i1, "reject")
        self.assertEqual(repo.add_candidate("rule", self._rule(), origin="d2"), 0)
        self.assertEqual(len(repo.candidates_list(True)), 0)

    def test_profile_and_filter_silenced_after_exists(self):
        i1 = repo.add_candidate("profile", {"statement": "周三上午写方案", "kind_type": "pattern"},
                                origin="d1")
        repo.decide_candidate(i1, "approve")
        self.assertEqual(repo.add_candidate("profile", {"statement": "周三上午写方案"},
                                            origin="d2"), 0)
        i3 = repo.add_candidate("filter", {"pattern": "SECRET_[A-Z]{8}",
                                           "category": "secret"}, origin="d1")
        repo.decide_candidate(i3, "approve")
        self.assertEqual(repo.add_candidate("filter", {"pattern": "SECRET_[A-Z]{8}",
                                                       "category": "secret"}, origin="d2"), 0)


class T08_Stream_Evidence(unittest.TestCase):
    def setUp(self):
        H.setup_home()

    def test_stream_consume_tolerates_garbage(self):
        from butler import distiller as D
        lines = ["not json at all",
                 '{"type":"system","subtype":"init"}',
                 json.dumps({"type": "assistant", "message":
                             {"content": [{"type": "text", "text": "hi"}]}}),
                 '{"broken',
                 json.dumps({"type": "result", "result": "ok",
                             "usage": {"output_tokens": 7}, "total_cost_usd": 0.1})]
        events = []
        res = D.stream_consume(iter(lines), events.append)
        self.assertEqual(len(events), 3)                 # 坏行/非 json 不回调
        self.assertEqual(res["usage"]["output_tokens"], 7)

    def test_evidence_reads_full_line(self):
        slug = config.ARCHIVE / "D--x"
        slug.mkdir(parents=True)
        sid = "abc123"
        (slug / f"{sid}.jsonl").write_text(
            H.user_msg("u1", "短证据行") + "\n"
            + H.user_msg("u2", "长原文" + "A" * 6000) + "\n", "utf-8")
        q_short = repo.evidence_quotes([{"session": sid, "line": 1, "quote": "快照"}])[0]
        self.assertEqual(q_short["quote"], "短证据行")
        self.assertTrue(q_short["full"])
        q_long = repo.evidence_quotes([{"session": sid, "line": 2, "quote": "快照"}])[0]
        self.assertIn("长原文", q_long["quote"])          # 读到文件全文（截4000）
        self.assertFalse(q_long["full"])

    def test_evidence_fast_path_via_slug(self):
        # sessions 有 slug 登记时走 O(1) 定位（不依赖 rglob 全库扫）
        sid = "fast123"
        slug_dir = config.CLAUDE_PROJECTS / "D--y"
        slug_dir.mkdir(parents=True)
        (slug_dir / f"{sid}.jsonl").write_text(
            H.user_msg("u1", "废话") + "\n" + H.user_msg("u2", "目标句") + "\n", "utf-8")
        db.conn().execute("INSERT INTO sessions(session_id,slug,total_lines) "
                          "VALUES(?,?,2)", (sid, "D--y"))
        db.conn().commit()
        q = repo.evidence_quotes([{"session": sid, "line": 2, "quote": "旧快照"}])[0]
        self.assertEqual(q["quote"], "目标句")
        self.assertTrue(q["full"])


class T09_Decide_Edited_Safety(unittest.TestCase):
    """"改后批准"各 kind 的数据安全：不得悄悄翻转 mode、清空旧例、丢编辑值、写占位符。"""

    def setUp(self):
        H.setup_home()

    def test_rewrite_keeps_mode_and_untouched_fields(self):
        cid = repo.add_candidate("rule", {"mode": "require", "statement": "旧条文",
                                          "ex_bad": "旧错", "ex_good": "旧对"})
        repo.decide_candidate(cid, "approve")                      # T1 require
        c2 = repo.add_candidate("rule", {"mode": "require", "statement": "旧条文",
                                         "ex_bad": "旧错", "ex_good": "旧对"},
                                dedup="violation_repeat", dedup_ref="T1")
        # 模拟旧版前端只传 statement：mode/正反例必须沿用旧值，不许被翻转清空
        self.assertTrue(repo.decide_candidate(c2, "approve",
                                              edited={"statement": "新条文"}))
        r = db.conn().execute("SELECT * FROM rules WHERE id='T1'").fetchone()
        self.assertEqual(r["mode"], "require")
        self.assertEqual(r["statement"], "新条文")
        self.assertEqual(r["ex_bad"], "旧错")
        self.assertEqual(r["ex_good"], "旧对")

    def test_rewrite_empty_statement_or_ghost_ref_refused(self):
        cid = repo.add_candidate("rule", {"mode": "forbid", "statement": "旧条文",
                                          "ex_bad": "a", "ex_good": "b"})
        repo.decide_candidate(cid, "approve")
        c2 = repo.add_candidate("rule", {"statement": "x"}, dedup="violation_repeat",
                                dedup_ref="T1")
        self.assertFalse(repo.decide_candidate(c2, "approve", edited={"statement": "  "}))
        self.assertEqual(db.conn().execute(
            "SELECT statement FROM rules WHERE id='T1'").fetchone()["statement"], "旧条文")
        self.assertEqual(db.conn().execute(
            "SELECT status FROM candidates WHERE id=?", (c2,)).fetchone()["status"], "pending")
        c3 = repo.add_candidate("rule", {"statement": "y"}, dedup="violation_repeat",
                                dedup_ref="T99")                    # ref 幻觉
        self.assertFalse(repo.decide_candidate(c3, "approve"))

    def test_filter_edit_pattern_applied_and_guards(self):
        cid = repo.add_candidate("filter", {"pattern": "OLD_[A-Z]{4}", "category": "secret"})
        # 编辑后的 pattern 必须真正入库（旧实现只读原始 pattern，编辑被静默丢弃）
        self.assertTrue(repo.decide_candidate(cid, "approve",
                                              edited={"pattern": "NEW_[A-Z]{4}",
                                                      "category": "pii"}))
        md = config.FILTER_RULES_PATH.read_text("utf-8")
        self.assertIn("NEW_[A-Z]{4}", md)
        filterpipe._compiled = None
        masked, cats = filterpipe.mask_text("前 NEW_ABCD 后")
        self.assertIn("pii", cats)
        # 空/坏 pattern 拒绝晋升
        c2 = repo.add_candidate("filter", {"pattern": "X" * 12})
        self.assertFalse(repo.decide_candidate(c2, "approve", edited={"pattern": ""}))
        c3 = repo.add_candidate("filter", {"pattern": "Y" * 12})
        self.assertFalse(repo.decide_candidate(c3, "approve", edited={"pattern": "([bad"}))

    def test_profile_kind_type_promotes(self):
        cid = repo.add_candidate("profile", {"statement": "周三上午写方案",
                                             "kind_type": "pattern"})
        repo.decide_candidate(cid, "approve")
        p = db.conn().execute("SELECT kind FROM profiles").fetchone()
        self.assertEqual(p["kind"], "pattern")                     # 不再被降级成 fact

    def test_reject_does_not_bump_digest_version(self):
        cid = repo.add_candidate("rule", {"mode": "forbid", "statement": "s",
                                          "ex_bad": "a", "ex_good": "b"})
        before = db.conn().execute("SELECT COUNT(*) n FROM digest_versions").fetchone()["n"]
        repo.decide_candidate(cid, "reject")
        after = db.conn().execute("SELECT COUNT(*) n FROM digest_versions").fetchone()["n"]
        self.assertEqual(before, after)


class T10_Collector_Edge(unittest.TestCase):
    def setUp(self):
        self.tmp = H.setup_home()

    def test_multi_block_note_ingest_keeps_all_blocks(self):
        # 一份 note 两个候选块：旧实现条件写反，第一块被后块覆盖静默丢失
        (config.INBOX / "note-m1.md").write_text(
            "kind: rule\nstatement: 第一条\ndedup: new\n"
            "kind: rule\nstatement: 第二条\ndedup: new\n", "utf-8")   # 块间无空行（旧代码丢第一块）
        from butler.collector import Collector
        Collector().sweep_inbox()
        cs = repo.candidates_list(True)
        self.assertEqual(len(cs), 2)
        self.assertEqual({c["payload"]["statement"] for c in cs}, {"第一条", "第二条"})

    def test_duplicate_filter_md_line_survives_sync(self):
        # md 手贴重复行：同步不炸 IntegrityError（`butler run` 起不来防线）
        txt = config.FILTER_RULES_PATH.read_text("utf-8")
        config.FILTER_RULES_PATH.write_text(txt + txt.splitlines()[1] + "\n", "utf-8")
        filterpipe.sync_md_to_db()                                 # 不抛异常
        self.assertTrue(db.conn().execute(
            "SELECT 1 FROM filter_rules LIMIT 1").fetchone())

    def test_removed_then_readded_pattern_revives(self):
        base = config.FILTER_RULES_PATH.read_text("utf-8")
        config.FILTER_RULES_PATH.write_text(base + "ZZTEST_[0-9]{4} ||| pii ||| t\n",
                                            "utf-8")
        filterpipe.sync_md_to_db()
        config.FILTER_RULES_PATH.write_text(base, "utf-8")          # 删掉
        filterpipe.sync_md_to_db()
        row = db.conn().execute("SELECT status FROM filter_rules WHERE "
                                "pattern='ZZTEST_[0-9]{4}'").fetchone()
        self.assertEqual(row["status"], "removed")
        config.FILTER_RULES_PATH.write_text(base + "ZZTEST_[0-9]{4} ||| pii ||| t\n",
                                            "utf-8")
        filterpipe.sync_md_to_db()                                  # 加回
        row = db.conn().execute("SELECT status FROM filter_rules WHERE "
                                "pattern='ZZTEST_[0-9]{4}'").fetchone()
        self.assertEqual(row["status"], "active")                   # 复活

    def test_collector_line_bookkeeping_with_blank_lines(self):
        slug = config.CLAUDE_PROJECTS / "D--bl"
        slug.mkdir()
        sp = slug / "bl-1.jsonl"
        sp.write_text(H.user_msg("u1", "一") + "\n\n" + H.asst_msg("a1", "二")
                      + "\n", "utf-8")
        from butler.collector import Collector
        Collector().sweep_once()
        row = db.conn().execute("SELECT * FROM sessions WHERE session_id='bl-1'").fetchone()
        self.assertEqual(row["total_lines"], 3)                     # 物理行数（含空行）
        # 蒸馏证据行号 = 物理行号，证据回放按同一口径读得到正确行
        q = repo.evidence_quotes([{"session": "bl-1", "line": 3, "quote": "快照"}])[0]
        self.assertEqual(q["quote"], "二")


if __name__ == "__main__":
    unittest.main(verbosity=2)
