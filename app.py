"""
Simple Azure AI Foundry Agent App
No Teams SDK, no complexity - just a clean agent interface
"""

from azure.identity import DefaultAzureCredential
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Azure AI Configuration - using the provided endpoint
ENDPOINT = os.getenv('AZURE_AI_ENDPOINT')
AGENT_ID = os.getenv('AZURE_AI_AGENT_ID')  # Will be set if not provided
API_VERSION = "v1"

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "https://learn.microsoft.com/api/mcp")
MCP_SERVER_LABEL = os.getenv("MCP_SERVER_LABEL", "mslearn")

# Azure Authentication
credential = DefaultAzureCredential()
current_thread_id = None

def get_auth_headers():
    """Get authorization headers for Azure AI API"""
    try:
        print("🔐 Attempting Azure authentication...")
        token = credential.get_token("https://ai.azure.com/.default").token
        print("✅ Azure token obtained successfully")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    except Exception as e:
        print(f"❌ Azure authentication error: {e}")
        print(f"❌ Exception type: {type(e).__name__}")
        
        # Fallback to API key if available
        api_key = os.getenv('AZURE_AI_API_KEY')
        if api_key:
            print("🔑 Using API key fallback")
            return {
                "api-key": api_key,
                "Content-Type": "application/json"
            }
def create_agent():
    global AGENT_ID
    if AGENT_ID:
        return AGENT_ID
    
    url = f"{ENDPOINT}/assistants?api-version={API_VERSION}"
    payload = {
        "model": "gpt-4o",
        "name": "Simple Agent",
        "description": "A simple AI assistant",
        "instructions": "You are a helpful AI assistant.",
        "tools": []
    }
    r = requests.post(url, headers=get_auth_headers(), json=payload, timeout=30)
    r.raise_for_status()
    agent = r.json()
    AGENT_ID = agent['id']
    print(f"✅ Agent created: {AGENT_ID}")
    return AGENT_ID

def create_thread():
    """Create a new conversation thread"""
    try:
        url = f"{ENDPOINT}/threads?api-version={API_VERSION}"
        print(f"🔗 Creating thread at: {url}")
        
        headers = get_auth_headers()
        print(f"🔑 Headers prepared successfully")
        
        response = requests.post(url, headers=headers, json={})
        print(f"📡 Response status: {response.status_code}")
        print(f"📄 Response text: {response.text}")
        
        if response.status_code in [200, 201]:
            thread_data = response.json()
            thread_id = thread_data['id']
            print(f"✅ Thread created successfully: {thread_id}")
            return thread_id
        else:
            print(f"❌ Error creating thread. Status: {response.status_code}")
            print(f"❌ Response: {response.text}")
            return None
    except Exception as e:
        print(f"💥 Exception in create_thread: {str(e)}")
        print(f"💥 Exception type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return None

def send_message(thread_id, message):
    """Send a message to the agent"""
    agent_id = create_agent()
    
    # Add message to thread
    url = f"{ENDPOINT}/threads/{thread_id}/messages?api-version={API_VERSION}"
    headers = get_auth_headers()
    
    message_data = {
        "role": "user",
        "content": message
    }
    
    response = requests.post(url, headers=headers, json=message_data)
    if response.status_code not in [200, 201]:
        print(f"Error adding message: {response.text}")
        return None
    
    # Create run
    run_url = f"{ENDPOINT}/threads/{thread_id}/runs?api-version={API_VERSION}"
    run_data = {
        "assistant_id": agent_id
    }
    
    run_response = requests.post(run_url, headers=headers, json=run_data)
    if run_response.status_code not in [200, 201]:
        print(f"Error creating run: {run_response.text}")
        return None
    
    run_id = run_response.json()['id']
    
    # Poll for completion
    import time
    max_attempts = 30
    for _ in range(max_attempts):
        status_url = f"{ENDPOINT}/threads/{thread_id}/runs/{run_id}?api-version={API_VERSION}"
        status_response = requests.get(status_url, headers=headers)
        
        if status_response.status_code == 200:
            run_data = status_response.json()
            status = run_data['status']
            if status == 'completed':
                # Get messages
                messages_url = f"{ENDPOINT}/threads/{thread_id}/messages?api-version={API_VERSION}"
                messages_response = requests.get(messages_url, headers=headers)
                
                if messages_response.status_code == 200:
                    messages = messages_response.json()['data']
                    # Return the latest assistant message
                    for msg in messages:
                        if msg['role'] == 'assistant':
                            content = msg['content'][0]['text']['value']
                            return content
                break
            elif status == 'requires_action':
                required_action = run_data['required_action']
                details = required_action.get("submit_tool_outputs") or required_action.get("submit_tool_approval") or {}
                calls = details.get("tool_calls", [])
                if calls:
                    tool_approvals = []
                    for tc in calls:
                        if tc.get("type") == "mcp":
                            tool_approvals.append({
                                "tool_call_id": tc["id"],
                                "approve": True,
                                "headers": {}
                            })
                    if tool_approvals:
                        submit_url = f"{ENDPOINT}/threads/{thread_id}/runs/{run_id}/submit_tool_outputs?api-version={API_VERSION}"
                        submit_response = requests.post(submit_url, headers=headers, json={"tool_approvals": tool_approvals}, timeout=30)
                        if submit_response.status_code != 200:
                            print(f"Error submitting tool approvals: {submit_response.text}")
                            break
                        continue
                print("❌ No tool calls to approve")
                break
            elif status in ['failed', 'cancelled', 'expired']:
                print(f"Run failed with status: {status}")
                break
        
        time.sleep(1)
    
    return "Sorry, I couldn't process your request at the moment."

@app.route('/')
def home():
    """Serve the main HR Policy Assistant interface optimized for Teams"""
    return send_from_directory('.', 'index.html')

@app.route('/chat', methods=['POST'])
def chat():
    """Handle chat messages"""
    global current_thread_id
    
    try:
        data = request.get_json()
        message = data.get('message', '')
        
        if not message:
            return jsonify({'error': 'No message provided'}), 400
        
        # Create thread if it doesn't exist
        if not current_thread_id:
            current_thread_id = create_thread()
            if not current_thread_id:
                return jsonify({'error': 'Failed to create conversation thread'}), 500
        
        # Send message and get response
        response = send_message(current_thread_id, message)
        
        if response:
            return jsonify({
                'response': response,
                'thread_id': current_thread_id
            })
        else:
            return jsonify({'error': 'Failed to get response from agent'}), 500
            
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'endpoint': ENDPOINT,
        'api_version': API_VERSION
    })

if __name__ == '__main__':
    print(f"🚀 Starting Simple AI Agent App")
    print(f"📡 Endpoint: {ENDPOINT}")
    print(f" API Version: {API_VERSION}")
    
    # Create agent on startup
    create_agent()
    print(f"🔗 Agent ID: {AGENT_ID}")
    
    # Get port from environment variable (Azure App Service uses this)
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    
    print(f"🌐 Server will run on port: {port}")
    print("=" * 50)
    app.run(debug=debug_mode, host='0.0.0.0', port=port)