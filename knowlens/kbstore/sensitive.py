# -*- coding: utf-8 -*-
"""Local, deterministic sensitive-content scanning (ported rule set).

No network or model calls: this gate runs before content can reach an
embedding or LLM provider. Decisions: pass < redact < review < block.
"""
import hashlib
import re
from dataclasses import dataclass, field

RULE_VERSION = "mvp-3"
PASS, REDACT, REVIEW, BLOCK = "pass", "redact", "review", "block"
_ORDER = {PASS: 1, REDACT: 2, REVIEW: 3, BLOCK: 4}
LEVELS = {PASS: "none", REDACT: "medium", REVIEW: "high", BLOCK: "high"}


@dataclass
class Finding:
    category: str
    rule_id: str
    level: str
    action: str
    confidence: float
    start: int
    end: int
    sample: str = ""


@dataclass
class Result:
    decision: str = PASS
    level: str = "none"
    findings: list = field(default_factory=list)
    redacted: str = ""
    signature: str = ""


def _luhn(number):
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 12:
        return False
    total, parity = 0, len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _cn_id_valid(number):
    number = number.upper()
    if not re.match(r"^\d{17}[\dX]$", number):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    checks = "10X98765432"
    total = sum(int(number[i]) * weights[i] for i in range(17))
    return number[17] == checks[total % 11]


# (id, category, level, action, pattern, validator)
RULES = [
    ("private_key", "credential", "high", BLOCK,
     re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----.*?"
                r"-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
                re.S), 1.0, None),
    ("bearer_token", "credential", "high", BLOCK,
     re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{16,}"), 0.99, None),
    ("jwt", "credential", "high", BLOCK,
     re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\."
                r"[a-zA-Z0-9_-]{8,}\b"), 0.98, None),
    ("database_url_password", "credential", "high", BLOCK,
     re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)"
                r"://[^\s:/]+:[^\s@/]+@[^\s]+"), 0.99, None),
    ("secret_assignment", "credential", "high", BLOCK,
     re.compile(r"(?im)\b(?:password|passwd|pwd|api[_-]?key|access[_-]?key|"
                r"secret(?:[_-]?key)?|client[_-]?secret)\b[\"']?\s*[:=]\s*"
                r"[\"']?[^\s\"'#,;]{8,}"), 0.92, None),
    ("known_api_key", "credential", "high", BLOCK,
     re.compile(r"\b(?:sk-[a-zA-Z0-9_-]{16,}|AKIA[A-Z0-9]{16}|"
                r"gh[opusr]_[A-Za-z0-9_]{20,})\b"), 0.97, None),
    ("cn_id_card", "personal_identity", "high", REDACT,
     re.compile(r"\b[1-9][0-9]{5}(?:18|19|20)[0-9]{2}(?:0[1-9]|1[0-2])"
                r"(?:0[1-9]|[12][0-9]|3[01])[0-9]{3}[0-9Xx]\b"), 0.99,
     _cn_id_valid),
    ("bank_card", "financial_account", "high", REDACT,
     re.compile(r"\b[1-9][0-9]{15,18}\b"), 0.94, _luhn),
    ("email", "contact", "medium", REDACT,
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), 0.96,
     None),
    ("cn_mobile", "contact", "medium", REDACT,
     re.compile(r"1[3-9][0-9]{9}"), 0.95, None),
    ("classification_mark", "sensitivity_mark", "high", REVIEW,
     re.compile(r"(?im)(?:^|[\s【\[(])(?:绝密|机密|仅限内部|内部资料|禁止外传|"
                r"未经授权不得传播|confidential|internal\s+only)"
                r"(?:$|[\s】\])：:])"), 0.90, None),
    ("redacted_email", "contact", "medium", REDACT,
     re.compile(r"\b[A-Za-z0-9][*•]{3}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), 1.0,
     None),
    ("redacted_mobile", "contact", "medium", REDACT,
     re.compile(r"\b1[3-9][0-9][*•]{4}[0-9]{4}\b"), 1.0, None),
    ("redacted_cn_id", "personal_identity", "high", REDACT,
     re.compile(r"\b[1-9][0-9]{5}[*•]{8}[0-9]{3}[0-9Xx]\b"), 1.0, None),
]

CONTEXT_SUBJECT = re.compile(r"(?:姓名|患者姓名|客户姓名|员工姓名|儿童姓名|"
                             r"未成年人姓名)\s*[:：]\s*[\u4e00-\u9fff]{2,4}")
CONTEXT_SENSITIVE = re.compile(r"(?:病历|诊断|确诊|住院|医嘱|检验报告|手术记录|"
                               r"用药记录|生育信息|银行账户|交易流水|征信|"
                               r"宗教信仰|性取向|犯罪记录|精准定位|行踪轨迹|"
                               r"住宿记录)")


def _mask(value, head=4, tail=4):
    value = value or ""
    if len(value) <= head + tail:
        return "*" * len(value)
    return value[:head] + "*" * (len(value) - head - tail) + value[-tail:]


def _mask_match(rule_id, text):
    if rule_id in ("email", "redacted_email"):
        local, _, domain = text.partition("@")
        return (local[:1] + "***@" + domain) if domain else "***"
    if rule_id in ("cn_mobile", "redacted_mobile"):
        return text[:3] + "****" + text[-4:]
    if rule_id in ("cn_id_card", "redacted_cn_id"):
        return text[:6] + "*" * (len(text) - 10) + text[-4:]
    if rule_id in ("bank_card",):
        return text[:6] + "*" * (len(text) - 10) + text[-4:]
    if rule_id == "secret_assignment":
        key, sep, value = text.partition("=")
        if not sep:
            key, sep, value = text.partition(":")
        return key + sep + _mask(value.strip(), 4, 4)
    if rule_id == "database_url_password":
        return re.sub(r"(://[^:/]+:)[^@]+(@)", r"\1****\2", text)
    return _mask(text, 4, 4)


def _signature(units, auto_redact, custom_terms):
    digest = hashlib.sha256()
    digest.update(RULE_VERSION.encode("utf-8"))
    digest.update(b"1" if auto_redact else b"0")
    for term in sorted(t for t in custom_terms or [] if t):
        digest.update(term.encode("utf-8"))
    for name, content in units:
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(content.encode("utf-8")).digest())
    return digest.hexdigest()


def scan(units, auto_redact=True, custom_terms=None, max_findings=200):
    """Scan ``[(unit_id, text), ...]``; returns redacted texts and findings."""
    if isinstance(units, str):
        units = [("text", units)]
    custom_terms = [t for t in (custom_terms or []) if t and t.strip()]
    findings, redacted_map = [], {}
    decision = PASS
    for unit_id, content in units:
        content = content or ""
        spans = []
        for rule in RULES:
            rule_id, category, level, action, pattern, confidence, validator = rule
            for match in pattern.finditer(content):
                value = match.group(0)
                if validator and not validator(value):
                    continue
                effective = action
                if effective == REDACT and not auto_redact:
                    effective = REVIEW
                spans.append((match.start(), match.end(), rule_id, category,
                              level, effective, confidence,
                              _mask_match(rule_id, value)))
                if _ORDER[effective] > _ORDER[decision]:
                    decision = effective
        # contextual combination (name + sensitive topic)
        if CONTEXT_SUBJECT.search(content) and CONTEXT_SENSITIVE.search(content):
            spans.append((0, 0, "identified_sensitive_context",
                          "sensitive_personal", "high", REVIEW, 0.82, ""))
            if _ORDER[REVIEW] > _ORDER[decision]:
                decision = REVIEW
        for term in custom_terms:
            for match in re.finditer(re.escape(term), content, re.I):
                spans.append((match.start(), match.end(), "custom_term",
                              "custom_term", "high", REVIEW, 0.9,
                              _mask(match.group(0), 1, 1)))
                if _ORDER[REVIEW] > _ORDER[decision]:
                    decision = REVIEW
        if spans:
            spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
            out, cursor = [], 0
            for start, end, rule_id, category, level, action, confidence, \
                    masked in spans:
                if end <= start:
                    continue
                if start < cursor:
                    continue
                out.append(content[cursor:start])
                out.append(masked if action == REDACT else content[start:end])
                cursor = end
                findings.append(Finding(category, rule_id, level, action,
                                        confidence, start, end,
                                        masked if action == REDACT else ""))
            out.append(content[cursor:])
            redacted_map[unit_id] = "".join(out)
        else:
            redacted_map[unit_id] = content
    if not auto_redact:
        # review/block findings keep the original text untouched
        redacted_map = {unit_id: content for unit_id, content in units}
    level = "none"
    if decision != PASS:
        level = "high" if decision in (REVIEW, BLOCK) else "medium"
    return Result(decision=decision, level=level, findings=findings[:max_findings],
                  redacted="\n\n".join(redacted_map.get(unit_id, "")
                                       for unit_id, _ in units),
                  signature=_signature(units, auto_redact, custom_terms))


def apply_gate(title, markdown, config):
    """Return (decision, markdown_or_redacted, result)."""
    config = config or {}
    if not config.get("enabled"):
        return PASS, markdown, None
    result = scan([("title", title or ""), ("body", markdown or "")],
                  auto_redact=config.get("auto_redact", True),
                  custom_terms=config.get("custom_terms") or [])
    if result.decision in (BLOCK, REVIEW):
        return result.decision, markdown, result
    if result.decision == REDACT:
        body = result.redacted.split("\n\n", 1)
        return REDACT, (body[1] if len(body) > 1 else markdown), result
    return PASS, markdown, result