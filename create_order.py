import os
import razorpay
from dotenv import load_dotenv

load_dotenv()

client = razorpay.Client(auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")))

order = client.order.create({
    "amount": 50000,   # amount in paise, so this is 500 rupees
    "currency": "INR",
    "receipt": "test_receipt_001",
    "payment_capture": 1
})

print("Order created successfully!")
print("Order ID:", order["id"])