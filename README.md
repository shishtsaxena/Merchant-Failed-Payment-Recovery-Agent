# merchant-failed-payment-recovery-agent
An agentic system built with LangGraph that detects failed Razorpay payments, diagnoses the failure cause (temporary vs permanent), suggests a recovery action, and executes it via Razorpay's test-mode API with human-in-the-loop approval.
