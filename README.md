# Merchant Failed-Payment Recovery Agent

An autonomous AI agent that detects failed payments on Razorpay, diagnoses *why* they failed using the payment gateway's own error data, and takes a recovery action — with a human merchant approving every step before anything real happens.

Built for **Razorpay AI Buildathon 2026 — Track 1: AI Growth & Agentic Commerce**.

---

## The Problem

When a payment fails on a merchant's platform, the merchant typically only sees a generic "payment failed" status. They don't know:
- Was this a temporary glitch (network timeout, gateway hiccup) or a permanent failure (card declined, insufficient funds)?
- What should they tell the customer to do next?
- Should they retry the same payment, or does the customer need a new payment link?

Today, this investigation is manual — a support agent digs through dashboards, or worse, the merchant just asks the customer to "try again" without knowing why it failed in the first place.

## What This Agent Does

Given a `transaction_id`, the agent:

1. **Fetches** the real payment record from Razorpay (status, error code, error reason, error description).
2. **Diagnoses** the failure using the gateway's actual error data — classifying it as `temporary` (safe to retry the same payment) or `permanent` (needs a new payment attempt), and generates a plain-English explanation a non-technical merchant or customer can understand.
3. **Suggests** valid alternative digital payment methods (UPI, Card, Net Banking, Wallet) when the failure is permanent — filtered to exclude illogical options like Cash on Delivery.
4. **Pauses for human approval** — the agent does not touch money or create orders without an explicit yes from the merchant. The merchant also specifies their preferred retry method at this step.
5. **Executes the recovery action** — either captures the original payment (temporary case) or creates a new Razorpay order (permanent case) — only after approval.
6. **Generates a retry checkout page** — a Razorpay-hosted checkout link restricted to the customer's preferred payment method, ready to be shared for completing the retry.

## Why Human-in-the-Loop

This agent can take real financial actions (capturing payments, creating orders) on a live payment gateway. Rather than letting the LLM act autonomously, the graph **interrupts and waits** at the approval step — the merchant sees exactly what the agent wants to do and must approve it before execution. This is a deliberate safety design, not a limitation.

## Architecture

Built with **LangGraph** as a stateful, interruptible agent graph:

```
fetch_payment_data
        │
        ▼
analyze_failure_reason  (LLM classifies: temporary / permanent + plain-English reason)
        │
        ├── temporary ──► suggest_retry
        │
        └── permanent ──► suggest_alternative (LLM suggests valid digital payment methods)
                    │
                    ▼
            human_approval  ⏸ (interrupt — waits for merchant yes/no + preferred method)
                    │
            ┌───────┴───────┐
            │               │
         approved        rejected
            │               │
            ▼               ▼
      execute_action       END
    (capture payment OR
     create new order +
     generate retry
     checkout page)
            │
            ▼
           END
```

State persistence and interrupt/resume is handled via LangGraph's `SqliteSaver` checkpointer, so the graph can pause mid-execution and resume later with the merchant's decision.

## Tech Stack

| Component | Choice |
|---|---|
| Agent orchestration | LangGraph |
| LLM | OpenAI `gpt-4o-mini` (via `langchain-openai`) |
| Payment gateway | Razorpay (test mode) — `razorpay` Python SDK |
| State persistence | `langgraph-checkpoint-sqlite` |
| Checkout UI | Razorpay hosted checkout widget (dynamically generated HTML) |

## Setup

### Prerequisites
- Python 3.10+
- A Razorpay account with test-mode API keys
- An OpenAI API key

### Installation

```bash
git clone <repo-url>
cd merchant-failed-payment-recovery-agent
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root:

```
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=your_test_secret_key
OPENAI_API_KEY=sk-xxxxxxxxxxxx
```

Get test-mode Razorpay keys from **Dashboard → Settings → API Keys → Test Mode**.

### Running the Agent

```bash
python main.py
```

The agent will:
1. Fetch the specified test transaction
2. Print a clean summary of the failure (amount, reason, suggested methods)
3. Prompt you in the terminal: `Approve this action? (yes/no)`
4. If approved, ask for your preferred retry payment method
5. Execute the recovery action and print a link to a locally-hosted retry checkout page

### Generating a Test Failed Payment

Use `create_order.py` and `checkout.html` (served locally, e.g. `python -m http.server 8080`) to generate a real failed payment via Razorpay's hosted checkout.

**Test card for a genuine decline:**
- Visa: `4100 2800 0009 0000`
- Mastercard: `5305 6200 0006 0000`
- Any future expiry, any CVV
- At checkout, explicitly select **"Failure"** when prompted (test-mode simulation)

## Example Output

```
==================================================
SUMMARY - Final result
==================================================
Transaction ID     : pay_TXjINf2SmjCTxG
Failed Amount      : ₹500.00
Failure Type       : permanent
Reason (in short)  : The payment could not be processed because it didn't
                      meet the requirements, like having insufficient funds
                      or an invalid card.
Suggested Methods  :
1. UPI - Quick and instant transfer using mobile apps linked to bank accounts.
2. Digital Wallet - Convenient and secure option for fast payments.
3. Credit Card - Allows for higher spending limits.
User Approval      : True
Preferred Method   : card
Execution Result   :
   id             : order_TXkNXRgRy7EH0z
   status         : created
Retry Payment Link : http://localhost:8080/retry_checkout.html
==================================================
```

## Design Decisions & Trade-offs

- **Human approval is non-negotiable.** No financial action executes without explicit merchant sign-off — this was a core requirement, not an afterthought.
- **Failure reasons come from Razorpay's own gateway data** (`error_code`, `error_reason`, `error_description`), not guessed by the LLM — the LLM's role is to translate that data into plain English and classify it, not invent a diagnosis.
- **Suggested alternatives are restricted to real digital retry methods** (UPI, Card, Net Banking, Wallet) — the LLM prompt explicitly excludes options like Cash on Delivery that don't make sense for an online payment retry.
- **UPI is excluded from the generated retry checkout page** specifically, since UPI intent/QR flows require a live mobile app and HTTPS deployment — not testable meaningfully on `localhost` for a demo.

## Future Improvements

- Replace terminal-based approval with a Streamlit dashboard (approve/reject buttons, live status)
- Deploy the retry checkout page publicly (HTTPS) to enable full UPI support
- Add a retry-count safeguard to avoid repeated failed attempts on the same transaction
- Persist per-transaction thread IDs so the same failed payment can be revisited without losing history

---

Built by Shisht Saxena for Razorpay AI Buildathon 2026.