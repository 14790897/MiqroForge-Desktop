
from miqi.execution.exec_policy import (
    CommandRule,
    ExecPolicy,
    FilesystemRule,
    NetworkRule,
    PolicyVerdict,
)


def test_command_rule_prefix_allow():
    policy = ExecPolicy(command_rules=[
        CommandRule(prefix=["git", "status"], decision="allow", source="builtin:git"),
    ])
    d = policy.evaluate_command("git status --short")
    assert d.verdict == PolicyVerdict.ALLOW
    assert d.source == "builtin:git"


def test_command_rule_deny_wins_over_allow():
    policy = ExecPolicy(command_rules=[
        CommandRule(prefix=["rm"], decision="allow", source="a"),
        CommandRule(prefix=["rm", "-rf"], decision="deny", source="b"),
    ])
    d = policy.evaluate_command("rm -rf /tmp/x")
    assert d.verdict == PolicyVerdict.DENY
    assert d.source == "b"


def test_command_no_match_returns_prompt():
    policy = ExecPolicy(command_rules=[])
    assert policy.evaluate_command("curl example.com").verdict == PolicyVerdict.PROMPT


def test_network_rule_match_protocol_host():
    policy = ExecPolicy(network_rules=[
        NetworkRule(protocol="tcp", host="api.openai.com", decision="allow", source="net:openai"),
    ])
    assert policy.evaluate_network("tcp", "api.openai.com", 443).verdict == PolicyVerdict.ALLOW
    assert policy.evaluate_network("tcp", "evil.test", 443).verdict == PolicyVerdict.PROMPT


def test_filesystem_rule_write_outside_workspace_denied():
    policy = ExecPolicy(filesystem_rules=[
        FilesystemRule(path_prefix="/etc", access="write", decision="deny", source="fs:etc"),
    ])
    assert policy.evaluate_filesystem("/etc/passwd", "write").verdict == PolicyVerdict.DENY


def test_amend_appends_without_replacing():
    policy = ExecPolicy(command_rules=[])
    policy.amend_command(CommandRule(prefix=["ls"], decision="allow", source="amend"))
    assert policy.evaluate_command("ls -la").verdict == PolicyVerdict.ALLOW


def test_from_config_builds_rules():
    cfg = {
        "command": [
            {"prefix": "git status", "decision": "allow", "source": "cfg"},
            {"prefix": "rm -rf", "decision": "deny", "source": "cfg"},
        ],
        "network": [
            {"protocol": "tcp", "host": "api.openai.com", "decision": "allow"},
        ],
    }
    policy = ExecPolicy.from_config(cfg)
    assert policy.evaluate_command("git status").verdict.value == "allow"
    assert policy.evaluate_command("rm -rf /").verdict.value == "deny"
    assert policy.evaluate_network("tcp", "api.openai.com", 443).verdict.value == "allow"
