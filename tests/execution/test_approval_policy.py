
from miqi.execution.approval_policy import ApprovalMode, ApprovalPolicy


def test_never_mode_auto_allows_all():
    p = ApprovalPolicy(mode=ApprovalMode.NEVER)
    assert p.requires_prompt(category="exec", failed=False) is False
    assert p.requires_prompt(category="file_write", failed=False) is False


def test_on_request_mode_always_prompts():
    p = ApprovalPolicy(mode=ApprovalMode.ON_REQUEST)
    assert p.requires_prompt(category="exec", failed=False) is True


def test_on_failure_mode_only_prompts_after_failure():
    p = ApprovalPolicy(mode=ApprovalMode.ON_FAILURE)
    assert p.requires_prompt(category="exec", failed=False) is False
    assert p.requires_prompt(category="exec", failed=True) is True


def test_unless_trusted_uses_trusted_categories():
    p = ApprovalPolicy(mode=ApprovalMode.UNLESS_TRUSTED, trusted={"exec"})
    assert p.requires_prompt(category="exec", failed=False) is False
    assert p.requires_prompt(category="file_write", failed=False) is True


def test_granular_per_category_map():
    p = ApprovalPolicy(
        mode=ApprovalMode.GRANULAR,
        granular={"exec": "never", "network": "on_request", "file_write": "on_failure"},
    )
    assert p.requires_prompt(category="exec", failed=False) is False
    assert p.requires_prompt(category="network", failed=False) is True
    assert p.requires_prompt(category="file_write", failed=False) is False
    assert p.requires_prompt(category="file_write", failed=True) is True


def test_unknown_category_defaults_to_prompt():
    p = ApprovalPolicy(mode=ApprovalMode.GRANULAR, granular={})
    assert p.requires_prompt(category="unknown_tool", failed=False) is True
