from pymongo import MongoClient, ReturnDocument
from pymongo.errors import ConnectionFailure, OperationFailure
from bson.objectid import ObjectId # Import ObjectId
import bcrypt
from typing import List, Dict, Optional, Any
import os
from dotenv import load_dotenv
import gridfs # For GridFS
from datetime import datetime
import io # For BytesIO
import logging # Import logging

load_dotenv()

# Configure logging for database.py
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO) # Or use the same config as main.py if more complex

class Database:
    def __init__(self):
        try:
            self.client = MongoClient(os.getenv("MONGO_URI"))
            # The ismaster command is cheap and does not require auth.
            self.client.admin.command('ismaster') 
            print("Successfully connected to MongoDB.")
        except ConnectionFailure:
            print("MongoDB server not available. Please check your MONGO_URI and ensure MongoDB is running.")
            # Handle connection error appropriately, e.g., raise an exception or exit
            raise
        
        self.db = self.client.email_campaign # Main database
        self.contacts = self.db.contacts     # Collection for contacts
        self.users = self.db.user            # Collection for users
        self.campaigns = self.db.campaigns   # Collection for campaigns
        self.email_tracking = self.db.email_tracking # Collection for tracking email opens
        self.smtp_credentials = self.db.smtp_credentials # Collection for user SMTP settings
        self.draft_emails = self.db.draft_emails # Collection for storing draft email content

        # For resumes, we'll use GridFS in the same database.
        # 'docs' will be the prefix for GridFS collections (docs.files, docs.chunks)
        self.fs = gridfs.GridFS(self.db, collection="docs") 
        # We'll use the 'filename', 'uploadDate', 'contentType', 'length' from GridFS's fs.files collection.
        # We'll add 'user_id' and any other custom metadata directly to the fs.files documents when uploading.

    def save_contacts(self, contacts: List[Dict]) -> Dict:
        """Save multiple contacts to the database"""
        try:
            result = self.contacts.insert_many(contacts)
            return {
                "success": True,
                "message": f"Successfully saved {len(result.inserted_ids)} contacts",
                "inserted_ids": result.inserted_ids
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error saving contacts: {str(e)}"
            }

    def get_contacts(self, user_id: Optional[str] = None) -> List[Dict]:
        """Get all contacts, optionally filtered by user_id, converting ObjectId to str"""
        query = {"user_id": user_id} if user_id else {}
        contacts_cursor = self.contacts.find(query)
        contacts_list = []
        for contact in contacts_cursor:
            if '_id' in contact:
                contact['_id'] = str(contact['_id'])
            # If there are other ObjectId fields, convert them here too
            # e.g., if contact had a 'related_object_id':
            # if 'related_object_id' in contact and isinstance(contact['related_object_id'], ObjectId):
            #     contact['related_object_id'] = str(contact['related_object_id'])
            contacts_list.append(contact)
        return contacts_list

    def create_user(self, name: str, email: str, password: str) -> Dict:
        """Create a new user with hashed password"""
        try:
            # Check if user already exists
            if self.users.find_one({"email": email}):
                return {
                    "success": False,
                    "message": "User with this email already exists"
                }

            # Hash the password
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

            # Insert the user
            result = self.users.insert_one({
                "name": name,
                "email": email,
                "password": hashed_password
            })

            return {
                "success": True,
                "message": "User created successfully",
                "user_id": str(result.inserted_id)
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error creating user: {str(e)}"
            }

    def verify_user(self, email: str, password: str) -> Dict:
        """Verify user credentials"""
        try:
            user = self.users.find_one({"email": email})
            if not user:
                return {
                    "success": False,
                    "message": "User not found"
                }

            if bcrypt.checkpw(password.encode('utf-8'), user['password']):
                return {
                    "success": True,
                    "message": "Login successful",
                    "user_id": str(user['_id']),
                    "name": user['name']
                }
            else:
                return {
                    "success": False,
                    "message": "Invalid password"
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error verifying user: {str(e)}"
            }

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """Get user details by user ID"""
        try:
            obj_id = ObjectId(user_id)
            user = self.users.find_one({"_id": obj_id})
            if user:
                user['_id'] = str(user['_id']) # Convert ObjectId to string
                # Don't return the password hash
                if 'password' in user:
                    del user['password']
                return user
            return None
        except Exception as e:
            logger.error(f"Error fetching user by ID {user_id}: {str(e)}")
            return None

    def delete_contact(self, contact_id: str) -> Dict:
        """Delete a contact by ID"""
        try:
            # Convert string contact_id to ObjectId for the query
            obj_id = ObjectId(contact_id)
            result = self.contacts.delete_one({"_id": obj_id})
            if result.deleted_count:
                return {
                    "success": True,
                    "message": "Contact deleted successfully"
                }
            return {
                "success": False,
                "message": "Contact not found"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error deleting contact: {str(e)}"
            }

    def update_contact(self, contact_id: str, update_data: Dict) -> Dict:
        """Update a contact's information"""
        try:
            # Ensure contact_id is ObjectId if it's for _id field
            # However, this generic update_contact might be used for other unique string IDs.
            # For now, assuming contact_id is a string that might need conversion if it's an _id.
            # If this method is *only* for _id, conversion should happen here or be guaranteed by caller.
            # Let's assume for now it's a generic update and _id conversion is handled by specific callers if needed.
            
            # If update_data contains '_id', it should not be changed.
            if '_id' in update_data:
                del update_data['_id']

            # If contact_id is indeed the string representation of _id
            try:
                query_filter = {"_id": ObjectId(contact_id)}
            except Exception: # If contact_id is not a valid ObjectId string, assume it's another unique field
                query_filter = {"contact_id_field_name": contact_id} # Replace with actual field if not _id

            result = self.contacts.update_one(
                query_filter,
                {"$set": update_data}
            )
            if result.modified_count:
                return {
                    "success": True,
                    "message": "Contact updated successfully"
                }
            # Check if document exists if no modification, to differentiate "not found" from "no changes"
            if self.contacts.count_documents(query_filter) > 0:
                 return {"success": True, "message": "No changes made to the contact."} # Data was same
            return {
                "success": False,
                "message": "Contact not found"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error updating contact: {str(e)}"
            }

    def update_contact_ref_tag(self, contact_id: str, ref_tag: Optional[str]) -> Dict:
        """Update or set the ref_tag for a specific contact."""
        try:
            obj_id = ObjectId(contact_id)
            update_operation = {"$set": {"ref_tag": ref_tag}} if ref_tag is not None else {"$unset": {"ref_tag": ""}}
            
            result = self.contacts.update_one(
                {"_id": obj_id},
                update_operation
            )
            
            if result.modified_count > 0:
                return {"success": True, "message": "Reference tag updated successfully."}
            elif result.matched_count > 0:
                 return {"success": True, "message": "Reference tag was already set to this value or no tag provided to remove."}
            else:
                return {"success": False, "message": "Contact not found."}
        except Exception as e:
            return {"success": False, "message": f"Error updating reference tag: {str(e)}"}

    # --- Resume Specific Methods ---

    def save_resume(self, user_id: str, filename: str, content_type: str, file_data: bytes) -> Dict[str, Any]:
        """Saves a resume file to GridFS and its metadata."""
        try:
            # GridFS put automatically handles chunking and saving to <collection_prefix>.files and <collection_prefix>.chunks
            # We store user_id and original_filename as metadata in the fs.files document.
            # GridFS itself stores filename, contentType, length, uploadDate.
            file_id = self.fs.put(
                file_data,
                filename=filename,  # Original filename, can be used for retrieval
                content_type=content_type,
                user_id=str(user_id), # Ensure user_id is stored as string if it's ObjectId elsewhere
                upload_date=datetime.utcnow() # Explicitly set upload_date for consistency
            )
            return {"success": True, "message": "Resume saved successfully.", "file_id": str(file_id)}
        except OperationFailure as e: # More specific MongoDB errors
            # Log the full error for debugging
            print(f"GridFS save operation failed: {e.details}")
            return {"success": False, "message": f"Database operation error saving resume: {e.details.get('errmsg', str(e))}"}
        except Exception as e:
            print(f"Error saving resume to GridFS: {str(e)}")
            return {"success": False, "message": f"An unexpected error occurred while saving resume: {str(e)}"}

    def get_resumes_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Retrieves a list of resume metadata for a given user."""
        resumes_list = []
        try:
            # Query the fs.files collection for documents matching the user_id
            # user_id was stored as metadata during fs.put
            for resume_file in self.fs.find({"user_id": str(user_id)}).sort("uploadDate", -1): # Sort by newest first
                resumes_list.append({
                    "_id": str(resume_file._id),  # This is the GridFS file ID
                    "filename": resume_file.filename,
                    "upload_date": resume_file.uploadDate, # GridFS stores this as uploadDate
                    "content_type": resume_file.contentType,
                    "length": resume_file.length
                })
            return resumes_list
        except Exception as e:
            print(f"Error fetching resumes for user {user_id}: {str(e)}")
            return [] # Return empty list on error

    def count_resumes_for_user(self, user_id: str) -> int:
        """Counts the number of resumes uploaded by a user."""
        try:
            # Count documents in fs.files that match the user_id
            count = self.db.docs.files.count_documents({"user_id": str(user_id)})
            return count
        except Exception as e:
            print(f"Error counting resumes for user {user_id}: {str(e)}")
            # Decide on error handling: raise, return -1, or 0. Returning 0 might be misleading.
            # Raising an exception might be better to signal a problem.
            # For now, let's re-raise so the caller (main.py) can handle it.
            raise 

    def get_resume_file_data(self, resume_id_str: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves a specific resume's file data (as BytesIO stream) and metadata 
        if it belongs to the user.
        """
        try:
            resume_obj_id = ObjectId(resume_id_str)
            # Find the GridFS file entry. Also check user_id for authorization.
            grid_out = self.fs.find_one({"_id": resume_obj_id, "user_id": str(user_id)})
            
            if grid_out:
                return {
                    "filename": grid_out.filename,
                    "content_type": grid_out.contentType,
                    "data_stream": grid_out.read(), # Returns the file content as bytes
                    "_id": str(grid_out._id)
                }
            return None
        except gridfs.errors.NoFile:
            return None # File not found
        except Exception as e:
            print(f"Error retrieving resume file data for resume_id {resume_id_str}, user {user_id}: {str(e)}")
            return None # Or re-raise

    def delete_resume_for_user(self, resume_id_str: str, user_id: str) -> bool:
        """Deletes a specific resume file from GridFS if it belongs to the user."""
        try:
            resume_obj_id = ObjectId(resume_id_str)
            # First, verify the resume exists and belongs to the user to prevent unauthorized deletion.
            # fs.exists can check by query.
            if self.fs.exists({"_id": resume_obj_id, "user_id": str(user_id)}):
                self.fs.delete(resume_obj_id)
                # Verify deletion (optional, fs.delete doesn't throw error if file not found by ID)
                # if not self.fs.exists(resume_obj_id):
                return True
                # else:
                #     print(f"Failed to verify deletion of resume {resume_id_str} for user {user_id}")
                #     return False # Deletion command issued but file might still exist
            else:
                # File not found or does not belong to the user
                return False
        except Exception as e:
            print(f"Error deleting resume {resume_id_str} for user {user_id}: {str(e)}")
            return False

    # --- Campaign Specific Methods ---

    def create_campaign(self, user_id: str, campaign_name: str, contact_ids_str: List[str]) -> Dict[str, Any]:
        """Creates a new campaign for a user with a list of contact IDs."""
        try:
            # Convert string contact_ids to ObjectId.
            # It's important that contact_ids_str contains valid ObjectId strings.
            contact_object_ids = []
            for cid_str in contact_ids_str:
                try:
                    contact_object_ids.append(ObjectId(cid_str))
                except Exception as e:
                    # Log or handle invalid ObjectId string format
                    print(f"Invalid ObjectId string for contact: {cid_str} - {e}")
                    # Depending on requirements, you might skip invalid ones or fail the whole operation
                    return {"success": False, "message": f"Invalid contact ID format: {cid_str}."}

            campaign_doc = {
                "user_id": ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id, # Store user_id as ObjectId if it is one
                "campaign_name": campaign_name.strip(),
                "contact_ids": contact_object_ids, # List of ObjectIds
                "created_at": datetime.utcnow(),
                "status": "draft" # Default status changed to draft
            }
            
            result = self.campaigns.insert_one(campaign_doc)
            
            if result.inserted_id:
                return {
                    "success": True, 
                    "message": "Campaign created successfully.", 
                    "campaign_id": str(result.inserted_id)
                }
            else:
                return {"success": False, "message": "Failed to create campaign (no ID returned)."}
                
        except OperationFailure as e:
            print(f"Database operation error creating campaign: {e.details}")
            return {"success": False, "message": f"Database error: {e.details.get('errmsg', str(e))}"}
        except Exception as e:
            print(f"Error creating campaign: {str(e)}")
            return {"success": False, "message": f"An unexpected error occurred: {str(e)}"}

    def get_campaigns_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Retrieves all campaigns for a given user."""
        campaigns_list = []
        try:
            query_user_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
            
            # Find campaigns matching the user_id, sort by newest first
            for campaign in self.campaigns.find({"user_id": query_user_id}).sort("created_at", -1):
                campaign["_id"] = str(campaign["_id"]) # Convert campaign ObjectId to string
                if "user_id" in campaign and isinstance(campaign["user_id"], ObjectId):
                    campaign["user_id"] = str(campaign["user_id"]) # Convert user_id ObjectId to string
                
                # Convert contact_ids from ObjectId to string for easier use in templates/frontend
                if "contact_ids" in campaign and isinstance(campaign["contact_ids"], list):
                    campaign["contact_ids"] = [str(cid) for cid in campaign["contact_ids"] if isinstance(cid, ObjectId)]
                
                campaigns_list.append(campaign)
            return campaigns_list
        except Exception as e:
            print(f"Error fetching campaigns for user {user_id}: {str(e)}")
            return [] # Return empty list on error

    def update_campaign_status(self, campaign_id: str, user_id: str, new_status: str) -> bool:
        """Updates the status of a campaign if it belongs to the user."""
        try:
            campaign_obj_id = ObjectId(campaign_id)
            user_obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id

            # Ensure the new_status is one of the allowed statuses if you have a predefined list
            # e.g., allowed_statuses = ["draft", "sending", "sent", "failed", "archived"]
            # if new_status not in allowed_statuses:
            #     logger.warning(f"Invalid status update: {new_status} for campaign {campaign_id}")
            #     return False

            result = self.campaigns.update_one(
                {"_id": campaign_obj_id, "user_id": user_obj_id},
                {"$set": {"status": new_status, "updated_at": datetime.utcnow()}}
            )
            if result.modified_count > 0:
                logger.info(f"Campaign {campaign_id} status updated to {new_status} for user {user_id}.")
                return True
            elif result.matched_count > 0:
                logger.info(f"Campaign {campaign_id} status already {new_status} or no change needed.")
                return True # No update needed but operation considered successful as state is correct
            else:
                logger.warning(f"Campaign {campaign_id} not found or access denied for user {user_id} during status update to {new_status}.")
                return False
        except Exception as e:
            logger.error(f"Error updating campaign status for {campaign_id} to {new_status}: {str(e)}")
            return False

    def get_campaign_by_id(self, campaign_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single campaign by its ID if it belongs to the user."""
        try:
            query_user_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
            campaign_obj_id = ObjectId(campaign_id)
            
            campaign = self.campaigns.find_one({"_id": campaign_obj_id, "user_id": query_user_id})
            
            if campaign:
                campaign["_id"] = str(campaign["_id"]) # Convert campaign ObjectId to string
                if "user_id" in campaign and isinstance(campaign["user_id"], ObjectId):
                    campaign["user_id"] = str(campaign["user_id"]) # Convert user_id ObjectId to string
                
                # Convert contact_ids from ObjectId to string
                if "contact_ids" in campaign and isinstance(campaign["contact_ids"], list):
                    campaign["contact_ids"] = [str(cid) for cid in campaign["contact_ids"] if isinstance(cid, ObjectId)]
                return campaign
            return None
        except Exception as e:
            print(f"Error fetching campaign {campaign_id} for user {user_id}: {str(e)}")
            return None

    def get_contacts_by_ids(self, contact_ids_str: List[str], user_id: str) -> List[Dict[str, Any]]:
        """Retrieves multiple contacts by their IDs if they belong to the user."""
        contacts_list = []
        try:
            contact_object_ids = [ObjectId(cid) for cid in contact_ids_str if ObjectId.is_valid(cid)]
            
            # user_id is already a string from the session, and contacts.user_id is stored as a string.
            query = {"_id": {"$in": contact_object_ids}, "user_id": user_id} 
            
            logger.info(f"Executing contacts query in get_contacts_by_ids with PyMongo: {query}") # Added detailed log
            
            found_contacts_cursor = self.contacts.find(query)
            for contact in found_contacts_cursor:
                if '_id' in contact: # Should always be true
                    contact['_id'] = str(contact['_id'])
                # user_id in contact doc is already string, no conversion needed for template
                contacts_list.append(contact)
            
            logger.info(f"get_contacts_by_ids found {len(contacts_list)} contacts for user {user_id} matching IDs {contact_ids_str}.") # Added detailed log
            return contacts_list
        except Exception as e:
            logger.error(f"Error in get_contacts_by_ids for user {user_id}, contact_ids {contact_ids_str}: {str(e)}", exc_info=True) # Enhanced log
            return []

    def delete_campaign_by_id(self, campaign_id: str, user_id: str) -> bool:
        """Deletes a campaign by its ID if it belongs to the user."""
        try:
            campaign_obj_id = ObjectId(campaign_id)
            query_user_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
            
            # Delete associated draft emails first
            self.delete_draft_emails_for_campaign(campaign_id, user_id)
            
            result = self.campaigns.delete_one({"_id": campaign_obj_id, "user_id": query_user_id})
            return result.deleted_count > 0
        except Exception as e:
            print(f"Error deleting campaign {campaign_id} for user {user_id}: {str(e)}")
            return False

    # --- Email Tracking Methods ---

    def delete_tracking_records_by_campaign_id(self, campaign_id: str, user_id: str) -> int:
        """Deletes all email tracking records for a given campaign_id and user_id. Returns count of deleted records."""
        try:
            campaign_obj_id = ObjectId(campaign_id) if ObjectId.is_valid(campaign_id) else campaign_id
            user_obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id

            result = self.email_tracking.delete_many({
                "campaign_id": campaign_obj_id,
                "user_id": user_obj_id 
            })
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error deleting tracking records for campaign {campaign_id}, user {user_id}: {str(e)}")
            return 0 # Return 0 or raise, depending on desired error handling

    def create_tracking_record(self, tracking_id: str, user_id: str, campaign_id: str, contact_id: str) -> bool:
        """Creates a record for tracking a sent email."""
        try:
            record = {
                "tracking_id": tracking_id, # Unique ID for this specific email send
                "user_id": ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id,
                "campaign_id": ObjectId(campaign_id) if ObjectId.is_valid(campaign_id) else campaign_id,
                "contact_id": ObjectId(contact_id) if ObjectId.is_valid(contact_id) else contact_id,
                "sent_at": datetime.utcnow(),
                "opened_at": None, # Timestamp when the email was first opened
                "open_count": 0,   # Count how many times the email was opened
                "last_opened_at": None, # Timestamp of the most recent open
                "status": "sent"   # Status: sent, opened, bounced, etc.
            }
            self.email_tracking.insert_one(record)
            logger.info(f"Created tracking record with ID: {tracking_id} for user {user_id}, campaign {campaign_id}")
            return True
        except Exception as e:
            logger.error(f"Error creating tracking record for {tracking_id}: {str(e)}")
            return False

    def mark_email_as_opened(self, tracking_id: str) -> bool:
        """Marks an email as opened by setting the opened_at timestamp if not already set 
        and incrementing the open count."""
        try:
            current_time = datetime.utcnow()
            
            # First, check if the tracking record exists
            tracking_record = self.email_tracking.find_one({"tracking_id": tracking_id})
            if not tracking_record:
                logger.warning(f"Tracking ID not found: {tracking_id}")
                return False
                
            # Update strategy: If opened_at is None, set it to current time (first open)
            # Always increment open_count and update last_opened_at
            result = self.email_tracking.update_one(
                {"tracking_id": tracking_id},
                {
                    "$set": {
                        "last_opened_at": current_time,
                        "status": "opened"
                    },
                    "$inc": {"open_count": 1},
                    "$setOnInsert": {"opened_at": current_time}  # Only sets if opened_at is null
                }
            )
            
            # If opened_at was null (first open), set it explicitly
            if tracking_record.get("opened_at") is None:
                self.email_tracking.update_one(
                    {"tracking_id": tracking_id},
                    {"$set": {"opened_at": current_time}}
                )
            
            success = result.matched_count > 0
            if success:
                logger.info(f"Tracked email open for tracking ID: {tracking_id}, open count: {tracking_record.get('open_count', 0) + 1}")
            return success
        except Exception as e:
            logger.error(f"Error marking email as opened for {tracking_id}: {str(e)}")
            return False
    
    def get_tracking_stats_for_campaign(self, campaign_id: str, user_id: str) -> Dict[str, Any]:
        """Get email tracking statistics for a specific campaign"""
        try:
            campaign_obj_id = ObjectId(campaign_id) if ObjectId.is_valid(campaign_id) else campaign_id
            user_obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
            
            # Get all tracking records for this campaign
            tracking_records = list(self.email_tracking.find({
                "campaign_id": campaign_obj_id,
                "user_id": user_obj_id
            }))
            
            # Convert ObjectId to string for each record
            for record in tracking_records:
                if "_id" in record:
                    record["_id"] = str(record["_id"])
                if "user_id" in record and isinstance(record["user_id"], ObjectId):
                    record["user_id"] = str(record["user_id"])
                if "campaign_id" in record and isinstance(record["campaign_id"], ObjectId):
                    record["campaign_id"] = str(record["campaign_id"])
                if "contact_id" in record and isinstance(record["contact_id"], ObjectId):
                    record["contact_id"] = str(record["contact_id"])
                # Ensure open_count exists to prevent attribute errors
                if "open_count" not in record:
                    record["open_count"] = 0
            
            # Calculate statistics
            total_sent = len(tracking_records)
            total_opened = sum(1 for r in tracking_records if r.get("opened_at") is not None)
            open_rate = round((total_opened / total_sent) * 100, 2) if total_sent > 0 else 0
            
            # Calculate average opens per opened email
            total_opens = sum(r.get("open_count", 0) for r in tracking_records)
            avg_opens_per_email = round(total_opens / total_opened, 2) if total_opened > 0 else 0
            
            # Find most recently opened email
            opened_records = [r for r in tracking_records if r.get("opened_at") is not None]
            most_recent_open = None
            if opened_records:
                most_recent = max(opened_records, key=lambda r: r.get("last_opened_at", r.get("opened_at")))
                most_recent_open = most_recent.get("last_opened_at", most_recent.get("opened_at"))
            
            return {
                "campaign_id": str(campaign_id),
                "total_sent": total_sent,
                "total_opened": total_opened,
                "open_rate": open_rate,
                "total_opens": total_opens,
                "avg_opens_per_email": avg_opens_per_email,
                "most_recent_open": most_recent_open,
                "tracking_records": tracking_records
            }
            
        except Exception as e:
            logger.error(f"Error getting tracking stats for campaign {campaign_id}, user {user_id}: {str(e)}")
            return {
                "campaign_id": str(campaign_id),
                "error": str(e),
                "total_sent": 0,
                "total_opened": 0,
                "open_rate": 0,
                "total_opens": 0,
                "avg_opens_per_email": 0,
                "tracking_records": []
            }
    
    def get_all_tracking_stats(self, user_id: str) -> Dict[str, Any]:
        """Get email tracking statistics for all campaigns of a user"""
        try:
            user_obj_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
            
            # First get all campaigns for this user
            campaigns = self.campaigns.find({"user_id": user_obj_id})
            campaign_stats = []
            
            for campaign in campaigns:
                campaign_id = campaign["_id"]
                campaign_name = campaign.get("campaign_name", "Unnamed Campaign")
                
                # Get stats for this campaign
                stats = self.get_tracking_stats_for_campaign(str(campaign_id), user_id)
                stats["campaign_name"] = campaign_name
                campaign_stats.append(stats)
            
            # Calculate overall statistics
            total_sent = sum(stats["total_sent"] for stats in campaign_stats)
            total_opened = sum(stats["total_opened"] for stats in campaign_stats)
            total_opens = sum(stats["total_opens"] for stats in campaign_stats)
            overall_open_rate = round((total_opened / total_sent) * 100, 2) if total_sent > 0 else 0
            
            return {
                "user_id": str(user_id),
                "total_campaigns": len(campaign_stats),
                "total_sent": total_sent,
                "total_opened": total_opened,
                "total_opens": total_opens,
                "overall_open_rate": overall_open_rate,
                "campaign_stats": campaign_stats
            }
            
        except Exception as e:
            logger.error(f"Error getting all tracking stats for user {user_id}: {str(e)}")
            return {
                "user_id": str(user_id),
                "error": str(e),
                "total_campaigns": 0,
                "total_sent": 0,
                "total_opened": 0,
                "overall_open_rate": 0,
                "campaign_stats": []
            }

    # --- SMTP Settings Methods ---

    def save_or_update_smtp_settings(self, user_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Saves or updates SMTP settings for a user. Enforces one setting per user."""
        try:
            # Ensure user_id is stored consistently as a string, matching session user_id
            query_user_id = user_id # Use user_id directly as string

            # Prepare the document to be saved/updated
            # It's good practice to explicitly define the fields
            smtp_doc = {
                "user_id": query_user_id,
                "host": settings.get("smtp_host"),
                "port": int(settings.get("smtp_port")), # Ensure port is integer
                "username": settings.get("smtp_username"),
                "password": settings.get("smtp_password"), # Consider encrypting this
                "updated_at": datetime.utcnow()
            }

            # Use update_one with upsert=True. This will insert if no document matches user_id,
            # or update the existing one if it matches. This enforces the one-credential-per-user limit.
            result = self.smtp_credentials.update_one(
                {"user_id": query_user_id},
                {"$set": smtp_doc},
                upsert=True
            )

            if result.upserted_id or result.modified_count > 0 or result.matched_count > 0:
                 # matched_count > 0 covers the case where settings were submitted but identical to stored ones
                return {"success": True, "message": "SMTP settings saved successfully."}
            else:
                 # This case should ideally not be reached with upsert=True unless there's an error
                return {"success": False, "message": "Failed to save SMTP settings."}

        except ValueError:
             return {"success": False, "message": "Invalid port number provided."}
        except OperationFailure as e:
            logger.error(f"Database operation error saving SMTP settings for user {user_id}: {e.details}", exc_info=True)
            return {"success": False, "message": f"Database error: {e.details.get('errmsg', str(e))}"}
        except Exception as e:
            logger.error(f"Error saving SMTP settings for user {user_id}: {str(e)}", exc_info=True)
            return {"success": False, "message": f"An unexpected error occurred: {str(e)}"}

    def get_smtp_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves SMTP settings for a given user."""
        try:
            # Query using user_id directly as a string
            query_user_id = user_id
            settings = self.smtp_credentials.find_one({"user_id": query_user_id})
            
            if settings:
                # Convert ObjectId fields to strings if needed for JSON serialization or frontend use
                if "_id" in settings:
                    settings["_id"] = str(settings["_id"])
                # user_id is stored as string, no conversion needed
                # if "user_id" in settings and isinstance(settings["user_id"], ObjectId):
                #     settings["user_id"] = str(settings["user_id"])
                # Consider *not* returning the password directly unless absolutely necessary
                # Or return a masked version/indicator that it's set.
                # For sending email, the backend needs the real password.
                return settings
            return None
        except Exception as e:
            logger.error(f"Error fetching SMTP settings for user {user_id}: {str(e)}", exc_info=True)
            return None

    # --- Draft Email Methods ---

    def store_draft_emails(self, campaign_id: str, user_id: str, email_drafts: List[dict]) -> bool:
        """
        Store generated email drafts for a campaign to avoid regenerating them every time.
        
        Each draft should have:
        - contact_id: str
        - generated_subject: str 
        - generated_body: str
        - contact_name: str (optional)
        
        Returns True if successful, False otherwise.
        """
        try:
            # First, delete any existing drafts for this campaign and user
            self.draft_emails.delete_many({
                "campaign_id": campaign_id,
                "user_id": user_id
            })
            
            # Prepare documents for insert
            draft_documents = []
            for draft in email_drafts:
                draft_doc = {
                    "campaign_id": campaign_id,
                    "user_id": user_id,
                    "contact_id": draft.get("contact_id"),
                    "generated_subject": draft.get("generated_subject"),
                    "generated_body": draft.get("generated_body"),
                    "contact_name": draft.get("contact_name", ""),
                    "created_at": datetime.utcnow()
                }
                draft_documents.append(draft_doc)
            
            if draft_documents:
                result = self.draft_emails.insert_many(draft_documents)
                return len(result.inserted_ids) > 0
            
            return False
        except Exception as e:
            logger.error(f"Error storing draft emails for campaign {campaign_id}, user {user_id}: {str(e)}")
            return False

    def get_draft_emails(self, campaign_id: str, user_id: str) -> List[dict]:
        """
        Retrieve stored draft emails for a campaign.
        
        Returns a list of draft emails with:
        - contact_id
        - generated_subject
        - generated_body
        - contact_name (if available)
        
        Returns empty list if none found or on error.
        """
        try:
            drafts = self.draft_emails.find({
                "campaign_id": campaign_id,
                "user_id": user_id
            })
            
            result = []
            for draft in drafts:
                # Convert ObjectId to strings for serialization
                if '_id' in draft:
                    draft['_id'] = str(draft['_id'])
                if 'contact_id' in draft and isinstance(draft['contact_id'], ObjectId):
                    draft['contact_id'] = str(draft['contact_id'])
                
                result.append({
                    "contact_id": draft["contact_id"],
                    "generated_subject": draft["generated_subject"],
                    "generated_body": draft["generated_body"],
                    "contact_name": draft.get("contact_name", "")
                })
            
            return result
        except Exception as e:
            logger.error(f"Error retrieving draft emails for campaign {campaign_id}, user {user_id}: {str(e)}")
            return []

    def get_contact_by_id(self, contact_id: str, user_id: str) -> Optional[Dict]:
        """Retrieves a single contact by ID if it belongs to the user."""
        try:
            # Convert string contact_id to ObjectId for the query
            contact_obj_id = ObjectId(contact_id) if ObjectId.is_valid(contact_id) else contact_id
            
            contact = self.contacts.find_one({
                "_id": contact_obj_id,
                "user_id": user_id
            })
            
            if contact:
                # Convert ObjectId to string
                if '_id' in contact:
                    contact['_id'] = str(contact['_id'])
                return contact
            return None
        except Exception as e:
            logger.error(f"Error retrieving contact {contact_id} for user {user_id}: {str(e)}")
            return None

    def update_draft_email(self, campaign_id: str, user_id: str, contact_id: str, subject: str, body: str, resume_id: str = None) -> bool:
        """
        Update a specific draft email for a contact in a campaign.
        
        Returns True if successful, False otherwise.
        """
        try:
            # Convert contact_id to ObjectId if it's a string representation of ObjectId
            contact_id_query = ObjectId(contact_id) if ObjectId.is_valid(contact_id) else contact_id
            
            update_data = {
                "generated_subject": subject,
                "generated_body": body,
                "updated_at": datetime.utcnow()
            }
            
            # Only add resume_id if it's provided and not empty
            if resume_id:
                update_data["resume_id"] = resume_id
            
            result = self.draft_emails.update_one(
                {
                    "campaign_id": campaign_id,
                    "user_id": user_id,
                    "contact_id": contact_id_query
                },
                {
                    "$set": update_data
                }
            )
            
            # Return true if document was updated
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating draft email for campaign {campaign_id}, contact {contact_id}, user {user_id}: {str(e)}")
            return False

    def delete_draft_emails_for_campaign(self, campaign_id: str, user_id: str) -> int:
        """
        Delete all draft emails for a campaign when the campaign is deleted.
        
        Returns number of deleted drafts.
        """
        try:
            result = self.draft_emails.delete_many({
                "campaign_id": campaign_id,
                "user_id": user_id
            })
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error deleting draft emails for campaign {campaign_id}, user {user_id}: {str(e)}")
            return 0

# Create a singleton instance
db = Database()
