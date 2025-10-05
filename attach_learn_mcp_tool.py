import os
import json
import requests
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

load_dotenv()

ENDPOINT = os.environ["AZURE_AI_ENDPOINT"].rstrip("/")
AGENT_ID = os.environ["AZURE_AI_AGENT_ID"]
API_VERSION = "v1"

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL")
MCP_SERVER_LABEL = os.environ.get("MCP_SERVER_LABEL")


def get_headers():
    # Prefer Managed Identity / AAD
    try:
        token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    except Exception:
        # Fallback to API key if provided
        api_key = os.getenv("AZURE_AI_API_KEY")
        if not api_key:
            raise RuntimeError("No Azure auth available. Set AZURE_AI_API_KEY or enable Managed Identity.")
        return {"api-key": api_key, "Content-Type": "application/json"}


def get_agent():
    url = f"{ENDPOINT}/assistants/{AGENT_ID}?api-version={API_VERSION}"
    r = requests.get(url, headers=get_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def update_agent_tools(tools):
    url = f"{ENDPOINT}/assistants/{AGENT_ID}?api-version={API_VERSION}"
    # Try partial update with tools only
    payload = {"tools": tools}
    r = requests.post(url, headers=get_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def ensure_learn_mcp_tool():
    agent = get_agent()
    tools = agent.get("tools", []) or []

    # Check if MCP tool with our label already exists
    for t in tools:
        if t.get("type") == "mcp" and t.get("server_label") == MCP_SERVER_LABEL:
            print(f"✅ MCP tool already present on agent with label '{MCP_SERVER_LABEL}'")
            return

    # Append new MCP tool definition for Microsoft Learn
    tools.append({
        "type": "mcp",
        "server_url": MCP_SERVER_URL,
        "server_label": MCP_SERVER_LABEL,
        # Optional: restrict which Learn tools are callable
        # "allowed_tools": ["microsoft_docs_search", "microsoft_docs_fetch"]
    })

    updated = update_agent_tools(tools)
    print("✅ MCP tool added to agent.")
    print(json.dumps([t for t in updated.get("tools", []) if t.get("type") == "mcp"], indent=2))


def create_thread():
    url = f"{ENDPOINT}/threads?api-version={API_VERSION}"
    r = requests.post(url, headers=get_headers(), json={}, timeout=30)
    r.raise_for_status()
    return r.json()['id']


def create_run(thread_id, message):
    url = f"{ENDPOINT}/threads/{thread_id}/messages?api-version={API_VERSION}"
    message_data = {"role": "user", "content": message}
    r = requests.post(url, headers=get_headers(), json=message_data, timeout=30)
    r.raise_for_status()
    
    run_url = f"{ENDPOINT}/threads/{thread_id}/runs?api-version={API_VERSION}"
    run_data = {"assistant_id": AGENT_ID}
    r = requests.post(run_url, headers=get_headers(), json=run_data, timeout=30)
    r.raise_for_status()
    return r.json()['id']


def poll_run(thread_id, run_id):
    url = f"{ENDPOINT}/threads/{thread_id}/runs/{run_id}?api-version={API_VERSION}"
    r = requests.get(url, headers=get_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def approve_pending_tool_calls(thread_id, run_id, required_action):
    details = required_action.get("submit_tool_outputs") or required_action.get("submit_tool_approval") or {}
    calls = details.get("tool_calls", [])
    if not calls:
        return False

    tool_approvals = []
    for tc in calls:
        if tc.get("type") == "mcp":
            tool_approvals.append({
                "tool_call_id": tc["id"],
                "approve": True,
                "headers": {}  # add any required headers for your MCP server here
            })

    url = f"{ENDPOINT}/threads/{thread_id}/runs/{run_id}/submit_tool_outputs?api-version={API_VERSION}"
    r = requests.post(url, headers=get_headers(), json={"tool_approvals": tool_approvals}, timeout=30)
    r.raise_for_status()
    return True


def test_mcp_functionality():
    print("🧪 Testing MCP functionality...")
    
    # Create thread
    thread_id = create_thread()
    print(f"Created test thread: {thread_id}")
    
    # Create run with test message
    run_id = create_run(thread_id, "What is Azure AI Foundry?")
    print(f"Created test run: {run_id}")
    
    # Poll for completion
    import time
    for _ in range(30):
        run = poll_run(thread_id, run_id)
        status = run['status']
        
        if status == 'completed':
            print("✅ MCP test successful - run completed")
            return True
        elif status == 'requires_action':
            print("🔄 Approving pending tool calls...")
            if approve_pending_tool_calls(thread_id, run_id, run['required_action']):
                continue
            else:
                print("❌ No tool calls to approve")
                break
        elif status in ['failed', 'cancelled', 'expired']:
            print(f"❌ Test run failed with status: {status}. Details: {run.get('last_error') or run.get('error')}")
            return False
        
        time.sleep(1)
    
    print("⏰ Test timed out")
    return False


if __name__ == "__main__":
    print(f"Target agent: {AGENT_ID}")
    print(f"Adding MCP tool: {MCP_SERVER_LABEL} -> {MCP_SERVER_URL}")
    ensure_learn_mcp_tool()
    
    # Test MCP functionality
    if test_mcp_functionality():
        print("🎉 MCP attachment and testing successful!")
    else:
        print("⚠️ MCP attachment completed but testing failed. Check agent configuration.")


