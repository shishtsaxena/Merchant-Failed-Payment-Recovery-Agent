from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command
from langchain_openai import ChatOpenAI

import os
import sqlite3
import razorpay

from dotenv import load_dotenv

import uuid
import webbrowser

import json
import threading
import http.server
import socketserver


# ---------- 0. Load env vars & create Razorpay client ----------
load_dotenv()

razorpay_client = razorpay.Client(auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")))

llm = ChatOpenAI(model="gpt-4o-mini")

# ---------- 1. State schema ----------
class AgentState(TypedDict):
    transaction_id: str
    merchant_id: str
    payment_data: Optional[dict]
    failure_reason: Optional[str]      # "temporary" | "permanent"
    failure_detail: Optional[str]      # NEW — beginner-friendly reason
    suggested_action: Optional[str]
    retry_count: int
    user_approval: Optional[bool]
    preferred_method: Optional[str]    # NEW — user's chosen retry method
    conversation_history: list
    checkout_url: Optional[str]


# ---------- 2. fetch_payment_data (tool node) ----------
def fetch_payment_data(state: AgentState) -> AgentState:
    txn_id = state["transaction_id"]

    # Razorpay test-mode API call
    response = razorpay_client.payment.fetch(txn_id)
    # response contains: status, amount, error_code, error_description, method, etc.

    state["payment_data"] = response
    return state

# ---------- 3. analyze_failure_reason (LLM node) ----------
def analyze_failure_reason(state: AgentState) -> AgentState:
    error_code = state["payment_data"].get("error_code")
    error_desc = state["payment_data"].get("error_description")
    error_reason = state["payment_data"].get("error_reason")

    prompt = f"""
    A payment failed with these gateway details:
    Error Code: {error_code}
    Error Reason: {error_reason}
    Error Description: {error_desc}

    Do two things:
    1. Classify this failure as exactly one of: "temporary" or "permanent"
       - "temporary" = a glitch that may succeed if retried (network issue, gateway timeout, bank server busy)
       - "permanent" = will not succeed on retry (card declined, insufficient funds, international card not allowed, wrong CVV, expired card)
    2. Write ONE simple, beginner-friendly sentence explaining WHY it failed, in plain language a non-technical person would understand. No jargon.

    Respond in EXACTLY this format, nothing else:
    CLASSIFICATION: <temporary or permanent>
    REASON: <simple one-sentence explanation>
    """
    result = llm.invoke(prompt)
    lines = result.content.strip().split("\n")

    classification = "permanent"
    reason_text = "Reason unavailable"
    for line in lines:
        if line.upper().startswith("CLASSIFICATION:"):
            classification = line.split(":", 1)[1].strip().lower()
        elif line.upper().startswith("REASON:"):
            reason_text = line.split(":", 1)[1].strip()

    state["failure_reason"] = classification
    state["failure_detail"] = reason_text
    return state

# ---------- 4. conditional router ----------
def route_after_analysis(state: AgentState) -> str:
    if state["failure_reason"] == "temporary":
        return "suggest_retry"
    return "suggest_alternative"


# ---------- 5a. suggest_retry (LLM node) ----------
def suggest_retry(state: AgentState) -> AgentState:
    prompt = f"Suggest a retry timing for this failure: {state['payment_data']}"
    result = llm.invoke(prompt)
    state["suggested_action"] = result.content
    return state


# ---------- 5b. suggest_alternative (LLM node) ----------
def suggest_alternative(state: AgentState) -> AgentState:
    prompt = f"""
    A customer's payment failed permanently for this reason: {state.get('failure_detail')}

    Suggest 2-3 alternative DIGITAL payment methods the customer could use to retry the payment.
    Only choose from: UPI, Net Banking, Debit Card, Credit Card, Digital Wallet.
    Do NOT suggest Cash on Delivery, bank transfer (NEFT/RTGS), cryptocurrency, or anything that isn't an instant online payment retry option.
    Keep it short — a numbered list of 2-3 methods with a one-line reason each.Specificly mention wallet , Card and Net banking.
    """
    result = llm.invoke(prompt)
    state["suggested_action"] = result.content
    return state


# ---------- 6. human_approval (interrupt node) ----------
def human_approval(state: AgentState) -> AgentState:
    decision = interrupt({
        "question": "Approve this suggested action?",
        "suggested_action": state["suggested_action"],
    })
    state["user_approval"] = decision.get("approved")
    state["preferred_method"] = decision.get("preferred_method")
    return state

def route_after_approval(state: AgentState) -> str:
    return "execute_action" if state["user_approval"] else END

# ---------- 7. execute_action (tool node) ----------
def execute_action(state: AgentState) -> AgentState:
    if state["failure_reason"] == "temporary":
        result = razorpay_client.payment.capture(
            state["transaction_id"],
            int(state["payment_data"]["amount"]),
            {"currency": state["payment_data"]["currency"]}
        )
    else:
        result = razorpay_client.order.create({
            "amount": int(state["payment_data"]["amount"]),
            "currency": state["payment_data"]["currency"],
            "receipt": state["transaction_id"],
            "notes": {"preferred_method": state.get("preferred_method", "not specified")}
        })
        checkout_url = generate_checkout_page(
            order_id=result["id"],
            amount=int(state["payment_data"]["amount"]),
            preferred_method=state.get("preferred_method", "upi")
        )
        state["checkout_url"] = checkout_url

    state["conversation_history"].append({"action_result": result})
    return state


def generate_checkout_page(order_id: str, amount: int, preferred_method: str):
    method_key = preferred_method.lower().strip()

    
    if "card" in method_key:
        method_key = "card"
    elif "net" in method_key or "bank" in method_key:
        method_key = "netbanking"
    elif "wallet" in method_key:
        method_key = "wallet"
    else:
        method_key = "card"  # safe fallback

    methods_json = json.dumps({
        "card": method_key == "card",
        "netbanking": method_key == "netbanking",
        "wallet": method_key == "wallet"
    })    
    key_id = os.getenv("RAZORPAY_KEY_ID")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Retry Payment</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
</head>
<body>
    <h2>Retry Payment - Order {order_id}</h2>
    <button id="pay-btn">Pay Now</button>

    <script>
    document.getElementById('pay-btn').onclick = function(e) {{
        var options = {{
            "key": "{key_id}",
            "amount": "{amount}",
            "currency": "INR",
            "order_id": "{order_id}",
            "name": "Merchant Retry Payment",
            "description": "Retry failed transaction",
            "method": {methods_json},
            "handler": function (response) {{
                alert("Payment successful: " + response.razorpay_payment_id);
            }},
            "theme": {{ "color": "#3399cc" }}
        }};
        var rzp = new Razorpay(options);
        rzp.open();
        e.preventDefault();
    }}
    </script>
</body>
</html>
"""

    folder = os.getcwd()
    file_path = os.path.join(folder, "retry_checkout.html")
    with open(file_path, "w") as f:
        f.write(html_content)

    PORT = 8080

    def start_server():
        try:
            os.chdir(folder)
            handler = http.server.SimpleHTTPRequestHandler
            with socketserver.TCPServer(("", PORT), handler) as httpd:
                httpd.serve_forever()
        except OSError as e:
            print(f"\n[Server error - port {PORT} might already be in use]: {e}\n")
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    url = f"http://localhost:{PORT}/retry_checkout.html"
    return url


# ---------- 8. Graph wiring ----------
graph = StateGraph(AgentState)

graph.add_node("fetch_payment_data", fetch_payment_data)
graph.add_node("analyze_failure_reason", analyze_failure_reason)
graph.add_node("suggest_retry", suggest_retry)
graph.add_node("suggest_alternative", suggest_alternative)
graph.add_node("human_approval", human_approval)
graph.add_node("execute_action", execute_action)

graph.set_entry_point("fetch_payment_data")
graph.add_edge("fetch_payment_data", "analyze_failure_reason")

graph.add_conditional_edges(
    "analyze_failure_reason",
    route_after_analysis,
    {"suggest_retry": "suggest_retry", "suggest_alternative": "suggest_alternative"}
)

graph.add_edge("suggest_retry", "human_approval")
graph.add_edge("suggest_alternative", "human_approval")

graph.add_conditional_edges(
    "human_approval",
    route_after_approval,
    {"execute_action": "execute_action", END: END}
)

graph.add_edge("execute_action", END)

# ---------- 9. Checkpointer (needed for interrupt/resume) ----------
conn = sqlite3.connect(database="razorpay.db", check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)
app = graph.compile(checkpointer=checkpointer)

# ---------- 10. Clean summary printer ----------
def print_summary(state: dict, stage: str = ""):
    print("\n" + "=" * 50)
    print(f"SUMMARY {('- ' + stage) if stage else ''}")
    print("=" * 50)
    payment_data = state.get("payment_data", {}) or {}
    amount = payment_data.get("amount")
    amount_display = f"₹{int(amount) / 100:.2f}" if amount else "Not available"

    print(f"Transaction ID     : {state.get('transaction_id')}")
    print(f"Failed Amount      : {amount_display}")
    print(f"Failure Type       : {state.get('failure_reason')}")
    print(f"Reason (in short)  : {state.get('failure_detail')}")
    if state.get("suggested_action"):
        print(f"Suggested Methods  :\n{state.get('suggested_action')}")
    print(f"User Approval      : {state.get('user_approval')}")
    if state.get("preferred_method"):
        print(f"Preferred Method   : {state.get('preferred_method')}")

    last_action = state.get("conversation_history", [])
    if last_action:
        action_result = last_action[-1].get("action_result", {})
        print("Execution Result   :")
        for key, value in action_result.items():
            print(f"   {key:<15}: {value}")

    if state.get("checkout_url"):
        print(f"Retry Payment Link : {state.get('checkout_url')}")

    print("=" * 50 + "\n")


# ---------- 11. Running it ----------
config = {"configurable": {"thread_id": str(uuid.uuid4())}}

result = app.invoke(
    {"transaction_id": "pay_TXlCfg981TCHOj", "merchant_id": "merch_001", "retry_count": 0, "conversation_history": []},
    config=config
)
print_summary(result, stage="Paused for human approval")

user_input = input("Approve this action? (yes/no): ").strip().lower()
approved = user_input == "yes"

preferred_method = None
if approved:
    preferred_method = input("Which payment method should be used? (e.g. Wallet / Card / Net Banking): ").strip()

final_result = app.invoke(
    Command(resume={"approved": approved, "preferred_method": preferred_method}),
    config=config
)
print_summary(final_result, stage="Final result")


if final_result.get("checkout_url"):
    input("\nPress Enter to exit (server will keep running until then)...")