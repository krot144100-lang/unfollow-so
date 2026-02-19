# check_env.py
from dotenv import load_dotenv
import os

load_dotenv()

print("\n" + "="*50)
print("🔍 CHECKING ENVIRONMENT VARIABLES")
print("="*50 + "\n")

vars_to_check = [
    "SECRET_KEY",
    "ADMIN_GRANT_KEY", 
    "DB_PATH",
    "FREE_CREDITS",
    "STARTER_PACK_CREDITS",
    "PAYMENT_ADDRESS_TRC20",
    "PORT",
    "FLASK_DEBUG"
]

all_ok = True

for var in vars_to_check:
    value = os.environ.get(var)
    if value:
        if var in ["SECRET_KEY", "ADMIN_GRANT_KEY"]:
            # Показываем только первые 10 символов
            display = value[:10] + "..." if len(value) > 10 else value
        else:
            display = value
        print(f"✅ {var:25} = {display}")
    else:
        print(f"❌ {var:25} = NOT SET!")
        all_ok = False

print("\n" + "="*50)
if all_ok:
    print("✅ All environment variables are set!")
else:
    print("❌ Some variables are missing. Check your .env file!")
print("="*50 + "\n")
