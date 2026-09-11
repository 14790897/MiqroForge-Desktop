from miqi.runtime.mcp_status_runtime import McpStatusRuntime


def test_mcp_status_runtime_lists_config_and_plugin_servers():
    runtime = McpStatusRuntime()
    runtime.replace_config_servers({
        "config-server": {"command": "echo", "args": ["ok"]},
    })
    runtime.replace_plugin_servers([
        {"name": "plugin-server", "command": "echo", "args": ["ok"]},
    ])
    statuses = runtime.list_statuses()
    names = {s.name for s in statuses}
    assert names == {"config-server", "plugin-server"}
    assert all(s.status in {"not_started", "ready"} for s in statuses)


def test_mcp_status_runtime_records_failure():
    runtime = McpStatusRuntime()
    runtime.mark_starting("server-a", thread_id=None)
    runtime.mark_failed("server-a", "boom", thread_id=None)
    status = runtime.list_statuses()[0]
    assert status.name == "server-a"
    assert status.status == "failed"
    assert status.error == "boom"
