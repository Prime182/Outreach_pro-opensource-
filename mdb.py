from pymongo import MongoClient
import bcrypt

MONGO_URI = "mongodb+srv://fastapi_user:1234@cluster0.kaxpb.mongodb.net/"
client = MongoClient(MONGO_URI)
db = client.email_campaign
contacts_collection = db.contacts
users_collection = db.user # Changed 'users' to 'user' to match request
# campaigns_collection = db.campaigns # Removed campaigns collection

# Example: insert a contact
# contacts_collection.insert_one({
#     "name": "Alice",
#     "company": "Stripe",
#     "email": "alice@stripe.com"
# })

# Define user details for insertion
user_name_to_insert = "New User"
user_email_to_insert = "new.user@example.com"
plain_password_to_insert = "securePassword123"

# Hash the password
hashed_password_to_insert = bcrypt.hashpw(plain_password_to_insert.encode('utf-8'), bcrypt.gensalt())

# Insert the user into the 'user' collection
try:
    user_insert_result = users_collection.insert_one({
        "name": user_name_to_insert,
        "email": user_email_to_insert,
        "password": hashed_password_to_insert
    })
    inserted_user_id = user_insert_result.inserted_id
    print(f"User '{user_name_to_insert}' with ID '{inserted_user_id}' inserted successfully into 'user' collection.")

    if inserted_user_id:
        # Insert a contact linked to this user
        contact_name = "Sample Contact"
        contact_email = "contact@example.com"
        contact_company = "Sample Company"
        try:
            contacts_collection.insert_one({
                "name": contact_name,
                "email": contact_email,
                "company": contact_company,
                "user_id": inserted_user_id  # Foreign key linking to the user
            })
            print(f"Contact '{contact_name}' linked to user ID '{inserted_user_id}' inserted successfully.")
        except Exception as contact_e:
            print(f"An error occurred while inserting contact: {contact_e}")

except Exception as e:
    print(f"An error occurred while inserting user: {e}")
