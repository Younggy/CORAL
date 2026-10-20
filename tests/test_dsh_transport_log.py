"""Offline transport diagnostics. No provider connection or research is started."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "examples/optiver_rv/runtime/dsh-transport-log.mjs"
RUNTIME = Path.home() / "Projects/evalforge/.evalforge/private/runtimes/dsh/0.2.0-rc.2"
SECRET = "DO_NOT_LOG_test_key_123"


def run_node(source):
    result = subprocess.run(
        ["node", "--import", str(HOOK), "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert SECRET not in result.stderr
    return result, [
        json.loads(line.removeprefix("[dsh-transport] "))
        for line in result.stderr.splitlines()
        if line.startswith("[dsh-transport] ")
    ]


@pytest.mark.parametrize(
    "code",
    [
        "UND_ERR_CONNECT_TIMEOUT",
        "ECONNRESET",
        "ECONNREFUSED",
        "ENOTFOUND",
        "EAI_AGAIN",
        "ERR_TLS_CERT_ALTNAME_INVALID",
        "CERT_HAS_EXPIRED",
    ],
)
def test_sanitized_cause_code(code):
    _, records = run_node(f"""
        import {{ channel }} from 'node:diagnostics_channel';
        const cause = Object.assign(new Error('{SECRET}'), {{code: '{code}',
            syscall: 'connect', errno: -60, stack: '{SECRET}', headers: {{key: '{SECRET}'}}}});
        const error = new TypeError('fetch failed {SECRET}', {{cause}});
        channel('undici:request:error').publish({{
            request: {{origin: 'https://api.deepseek.com', path: '/?key={SECRET}',
                headers: 'Authorization: Bearer {SECRET}', body: '{SECRET}'}}, error
        }});
    """)
    assert len(records) == 1
    assert records[0]["causes"] == [
        {"type": "TypeError", "code": "UNKNOWN"},
        {"type": "Error", "code": code, "syscall": "connect", "errno": -60},
    ]
    assert set(records[0]) == {"time", "provider", "event", "causes"}


def test_untrusted_fields_aggregate_cycle_and_depth():
    _, records = run_node(f"""
        import {{ channel }} from 'node:diagnostics_channel';
        const a = {{name: '{SECRET}', code: '{SECRET}', syscall: '{SECRET}', errno: '{SECRET}'}};
        const b = new Error('{SECRET}');
        Object.defineProperty(b, 'code', {{get() {{throw new Error('{SECRET}')}}}});
        a.cause = a;
        const many = Array.from({{length: 100}}, () => ({{code: 'ECONNRESET'}}));
        const error = new AggregateError([a, b, ...many], '{SECRET}');
        channel('undici:request:error').publish({{request: {{origin: 'https://api.deepseek.com'}}, error}});
    """)
    assert len(records) == 1
    assert len(records[0]["causes"]) == 8
    assert records[0]["causes"][1] == {"type": "UNKNOWN", "code": "UNKNOWN"}


def test_unrelated_and_malformed_events_are_ignored():
    result, records = run_node("""
        import { channel } from 'node:diagnostics_channel';
        const events = channel('undici:request:error');
        for (const origin of ['https://other.example', 'https://api.deepseek.com.evil',
            'http://api.deepseek.com', 'invalid']) {
            events.publish({request: {origin}, error: {code: 'ECONNRESET'}});
        }
        events.publish(null);
        events.publish({get request() {throw new Error('ignored')}});
        console.log('still running');
    """)
    assert records == []
    assert result.stderr == ""
    assert result.stdout.strip() == "still running"


def test_real_provider_fetch_failure_keeps_transport_semantics():
    # Replace the socket connector, not the diagnostics event or provider catch.
    # Even an accidental unmocked URL cannot reach the network through this Agent.
    result, records = run_node(f"""
        import assert from 'node:assert/strict';
        import {{ createRequire }} from 'node:module';
        import {{ pathToFileURL }} from 'node:url';
        const require = createRequire({json.dumps(str(RUNTIME / "package.json"))});
        const {{ Agent, setGlobalDispatcher }} = require('undici');
        const {{ DeepSeekAdapter }} = await import(pathToFileURL(
            {json.dumps(str(RUNTIME / "node_modules/@deepseek-ai/dsh-llm-deepseek/lib/index.js"))}));
        let connections = 0;
        const cause = Object.assign(new Error('{SECRET}'), {{code: 'UND_ERR_CONNECT_TIMEOUT'}});
        const dispatcher = new Agent({{connect(_options, callback) {{
            connections++; callback(cause, null);
        }}}});
        setGlobalDispatcher(dispatcher);
        const adapter = new DeepSeekAdapter({{
            options: () => ({{baseURL: 'https://api.deepseek.com/anthropic',
                models: [], defaults: {{}}, maxTokens: 10, streamIdleTimeoutMs: 300000}}),
            resolveAuth: async () => ({{headers: {{authorization: 'Bearer {SECRET}'}}}}),
            resolveUserId: () => 'offline-test',
            prepareExtensions: async () => ({{fields: {{}}, accept: async () => {{}}}}),
        }});
        let caught;
        try {{
            for await (const _ of adapter.stream({{provider: 'deepseek-official',
                model: 'deepseek-chat', messages: [], tools: []}})) {{}}
        }} catch (error) {{caught = error}}
        assert.equal(connections, 1);
        assert.equal(caught.code, 'TRANSPORT');
        assert.equal(caught.message, 'DeepSeek Messages transport failed');
        assert.equal(caught.cause.cause, cause);
        await dispatcher.close();
        console.log('provider TRANSPORT unchanged; connector calls=1; network sockets=0');
    """)
    assert len(records) == 1
    assert records[0]["causes"][0]["code"] == "UND_ERR_CONNECT_TIMEOUT"
    assert "network sockets=0" in result.stdout


def test_diagnostic_write_failure_does_not_throw():
    result, _ = run_node("""
        import { closeSync } from 'node:fs';
        import { channel } from 'node:diagnostics_channel';
        closeSync(2);
        channel('undici:request:error').publish({request: {origin: 'https://api.deepseek.com'},
            error: {code: 'ECONNRESET'}});
        console.log('still running');
    """)
    assert result.stdout.strip() == "still running"


def test_successful_fetch_has_no_error_log():
    result, records = run_node(f"""
        import assert from 'node:assert/strict';
        import {{ createRequire }} from 'node:module';
        const require = createRequire({json.dumps(str(RUNTIME / "package.json"))});
        const {{ MockAgent, setGlobalDispatcher }} = require('undici');
        const dispatcher = new MockAgent();
        dispatcher.disableNetConnect();
        dispatcher.get('https://api.deepseek.com').intercept({{
            path: '/anthropic/messages', method: 'POST',
        }}).reply(200, 'offline response');
        setGlobalDispatcher(dispatcher);
        const response = await fetch('https://api.deepseek.com/anthropic/messages', {{method: 'POST'}});
        assert.equal(response.status, 200);
        assert.equal(await response.text(), 'offline response');
        await dispatcher.close();
        console.log('success unchanged');
    """)
    assert records == []
    assert result.stderr == ""
    assert result.stdout.strip() == "success unchanged"


def test_launcher_forwards_cli_arguments():
    result = subprocess.run(
        [str(HOOK.parent / "dsh"), "--help"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "--profile" in result.stdout
    assert "[dsh-transport]" not in result.stderr
