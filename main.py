from fastapi import FastAPI, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import BackgroundTasks # For background email sending
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, Response # Added Response for pixel
from pydantic import BaseModel # Import BaseModel
from starlette.middleware.sessions import SessionMiddleware # Added for sessions
import requests
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import uuid # For generating unique tracking IDs
from dotenv import load_dotenv
from database import db
from typing import List, Optional
import logging
from datetime import datetime
import io # For BytesIO
from bson import ObjectId # If resume IDs are ObjectIds, for validation/conversion
import mimetypes # For guessing content type if needed
from jinja2 import Template # For simple template rendering for email body
import google.generativeai as genai # Import Gemini library
import json # Added for parsing Gemini response

# Configure logging
import os
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("email_app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Set base URL for tracking pixels
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000/")
# Ensure it ends with a slash
if not PUBLIC_BASE_URL.endswith('/'):
    PUBLIC_BASE_URL += '/'
logger.info(f"Using base URL for tracking pixels: {PUBLIC_BASE_URL}")

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY not found in .env file. AI generation will not work.")
    # Optionally disable AI features or handle gracefully
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        logger.info("Gemini API configured successfully.")
    except Exception as e:
        logger.error(f"Failed to configure Gemini API: {e}")
        # Handle configuration error

# Initialize Hunter.io API key
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
if not HUNTER_API_KEY:
    logger.warning("HUNTER_API_KEY not found in .env file. Hunter.io API will not work.")

app = FastAPI()

# Decorator for requiring login
def login_required(func):
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("user_id"):
            return RedirectResponse(url="/login", status_code=302)
        return await func(request, *args, **kwargs)
    # Preserve original function's name and other attributes for FastAPI routing
    import functools
    functools.update_wrapper(wrapper, func)
    return wrapper

# Add Session Middleware
# IMPORTANT: Make sure SECRET_KEY is set in your .env file
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    logger.warning("SECRET_KEY not found in .env file. Session middleware might not work securely.")
    # Fallback for development if not set, but should be set for production
    SECRET_KEY = "a_default_fallback_secret_key_for_development_only"
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Mount static files BEFORE initializing templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
# Explicitly add url_for to Jinja environment globals
templates.env.globals['url_for'] = app.url_path_for


# Initialize Hunter.io API key
 # This should ideally also be in .env

# Resume constants
MAX_RESUME_SIZE_MB = 2
MAX_RESUME_SIZE_BYTES = MAX_RESUME_SIZE_MB * 1024 * 1024
ALLOWED_RESUME_CONTENT_TYPE = "application/pdf"
MAX_RESUMES_PER_USER = 2

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request, "user_name": request.session.get("name")})

@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/my-contacts", response_class=HTMLResponse)
async def my_contacts_page(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    # The /contacts API endpoint will be called by JS on this page
    return templates.TemplateResponse("contacts.html", {"request": request, "user_name": request.session.get("name")})

@app.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    
    try:
        contacts = db.get_contacts(user_id) # Fetch contacts for the current user
    except Exception as e:
        logger.error(f"Error fetching contacts for user {user_id} on campaigns page: {e}")
        contacts = []
        # Optionally, set a flash message for the error
        request.session["flash_message"] = "Could not load your contacts for campaign creation."
        request.session["flash_category"] = "error"

    flash_message = request.session.pop("flash_message", None)
    flash_category = request.session.pop("flash_category", None)

    try:
        user_campaigns = db.get_campaigns_for_user(user_id)
    except Exception as e:
        logger.error(f"Error fetching campaigns for user {user_id}: {e}")
        user_campaigns = []
        # Optionally, set a flash message for this error too
        request.session["flash_message"] = "Could not load your existing campaigns."
        request.session["flash_category"] = "error"
        # Re-fetch flash messages if one was just set
        flash_message = request.session.pop("flash_message", flash_message) # Keep original if not overwritten
        flash_category = request.session.pop("flash_category", flash_category)


    return templates.TemplateResponse("campaigns.html", {
        "request": request, 
        "user_name": request.session.get("name"),
        "contacts": contacts, # Pass contacts to the template
        "campaigns": user_campaigns, # Pass campaigns to the template
        "flash_message": flash_message,
        "flash_category": flash_category
    })

@app.post("/create-campaign", name="create_campaign")
async def create_campaign_route(
    request: Request, 
    campaign_name: str = Form(...), 
    selected_contacts: List[str] = Form(...) # This will be a list of contact_ids
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not campaign_name.strip():
        request.session["flash_message"] = "Campaign name cannot be empty."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

    if not selected_contacts:
        request.session["flash_message"] = "Please select at least one contact for the campaign."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

    try:
        # Assuming db.create_campaign will handle the logic of creating the campaign
        # It should take user_id, campaign_name, and the list of contact_ids
        result = db.create_campaign(user_id, campaign_name, selected_contacts)
        
        if result.get("success"):
            request.session["flash_message"] = result.get("message", "Campaign created successfully!")
            request.session["flash_category"] = "success"
            campaign_id = result.get("campaign_id") # Assume db.create_campaign returns this
            if campaign_id:
                # Redirect to the start campaign page for the new campaign
                logger.info(f"Campaign {campaign_id} created successfully for user {user_id}. Redirecting to start_campaign_page.")
                return RedirectResponse(url=request.url_for("start_campaign_page", campaign_id=str(campaign_id)), status_code=303)
            else:
                # Fallback if campaign_id is not returned
                logger.error(f"Campaign created successfully for user {user_id} but no campaign_id returned from db.create_campaign.")
                # Stay on campaigns page with success message
                return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
        else: # Campaign creation failed
            request.session["flash_message"] = result.get("message", "Failed to create campaign.")
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
            
    except Exception as e:
        logger.error(f"Error creating campaign for user {user_id}: {e}")
        request.session["flash_message"] = "An unexpected error occurred while creating the campaign."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

@app.post("/campaign/{campaign_id}/delete", name="delete_campaign")
async def delete_campaign_route(request: Request, campaign_id: str):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        # First, delete associated tracking records
        deleted_tracking_count = db.delete_tracking_records_by_campaign_id(campaign_id, user_id)
        logger.info(f"Deleted {deleted_tracking_count} tracking records for campaign {campaign_id} by user {user_id}.")

        # Then, delete the campaign itself
        campaign_deleted = db.delete_campaign_by_id(campaign_id, user_id)

        if campaign_deleted:
            request.session["flash_message"] = "Campaign and associated tracking data deleted successfully."
            request.session["flash_category"] = "success"
        else:
            # This might happen if the campaign was already deleted or doesn't belong to the user
            request.session["flash_message"] = "Failed to delete campaign. It might have already been deleted or you don't have permission."
            request.session["flash_category"] = "error"
            
    except Exception as e:
        logger.error(f"Error deleting campaign {campaign_id} for user {user_id}: {e}")
        request.session["flash_message"] = "An unexpected error occurred while deleting the campaign."
        request.session["flash_category"] = "error"
    
    return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)


@app.get("/campaign/{campaign_id}/start", response_class=HTMLResponse, name="start_campaign_page")
async def start_campaign_page(request: Request, campaign_id: str):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    try:
        logger.info(f"--- Debugging /campaign/{campaign_id}/start ---")
        logger.info(f"Session user_id: {user_id}")

        # Fetch campaign details
        campaign = db.get_campaign_by_id(campaign_id, user_id)
        if not campaign:
            logger.warning(f"Campaign {campaign_id} not found or access denied for user {user_id}.")
            raise HTTPException(status_code=404, detail="Campaign not found or access denied.")
        
        # Check if campaign has already been sent to prevent reentry
        current_status = campaign.get("status", "draft")
        if current_status in ["sending", "sent"]:
            logger.info(f"User {user_id} attempted to view start page for campaign {campaign_id} which is already in status: {current_status}.")
            request.session["flash_message"] = f"Campaign '{campaign.get('campaign_name', '')}' has already been sent (status: {current_status})."
            request.session["flash_category"] = "info"
            # Allow viewing the page rather than redirecting so they can see the details but can't resend
            # The UI will handle disabling send controls
        
        logger.info(f"Fetched campaign document: {campaign}")
        campaign_contact_ids_from_db = campaign.get("contact_ids", []) # These should be strings
        logger.info(f"Contact IDs from campaign document (should be list of strings): {campaign_contact_ids_from_db}")

        # Fetch contact details for the campaign
        contacts_in_campaign = []
        if campaign_contact_ids_from_db:
            logger.info(f"Attempting to fetch contacts with IDs: {campaign_contact_ids_from_db} for user_id: {user_id}")
            contacts_in_campaign = db.get_contacts_by_ids(campaign_contact_ids_from_db, user_id)
            logger.info(f"Result of db.get_contacts_by_ids: {contacts_in_campaign}")
            if not contacts_in_campaign and campaign_contact_ids_from_db:
                logger.warning(f"No contacts found for user {user_id} with IDs {campaign_contact_ids_from_db}, though campaign doc listed them.")
        else:
            logger.info("Campaign document has no contact_ids listed.")
            
        # Fetch user's resumes
        user_resumes = db.get_resumes_for_user(user_id)

        # --- Check for stored draft email content first ---
        generated_emails = []
        stored_drafts = db.get_draft_emails(campaign_id, user_id)
        
        if stored_drafts:
            logger.info(f"Found {len(stored_drafts)} stored draft emails for campaign {campaign_id}")
            generated_emails = stored_drafts
        elif contacts_in_campaign:
            # No stored drafts found, generate new ones
            user_name = request.session.get("name") # Need user name for generation
            if user_name:
                try:
                    logger.info(f"No stored drafts found. Generating new email content for campaign {campaign_id} (User: {user_id})")
                    generated_emails = await _generate_emails_for_contacts(user_id, user_name, contacts_in_campaign)
                    logger.info(f"Generated {len(generated_emails)} emails for campaign {campaign_id}")
                    
                    # Store the newly generated emails for future use
                    if generated_emails:
                        storage_success = db.store_draft_emails(campaign_id, user_id, generated_emails)
                        if storage_success:
                            logger.info(f"Successfully stored {len(generated_emails)} draft emails for campaign {campaign_id}")
                        else:
                            logger.warning(f"Failed to store draft emails for campaign {campaign_id}")
                except Exception as gen_e:
                    logger.error(f"Error generating emails during page load for campaign {campaign_id}: {gen_e}")
                    generated_emails = [] 
                    request.session["flash_message"] = "Failed to generate email content. Please try generating manually or check logs."
                    request.session["flash_category"] = "warning"
            else:
                logger.warning(f"Cannot generate emails for campaign {campaign_id}: user_name not found in session.")
                request.session["flash_message"] = "Could not retrieve user details to generate emails."
                request.session["flash_category"] = "error"

    except HTTPException as http_exc:
        raise http_exc # Re-raise 404 if campaign not found
    except Exception as e:
        logger.error(f"Error loading start campaign page for campaign {campaign_id}, user {user_id}: {e}")
        # Redirect back with an error message
        request.session["flash_message"] = "Could not load campaign details. Please try again."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

    return templates.TemplateResponse("start_campaign.html", {
        "request": request,
        "user_name": request.session.get("name"),
        "campaign": campaign,
        "contacts": contacts_in_campaign,
        "resumes": user_resumes,
        "generated_emails": generated_emails # Pass generated emails to the template
    })

# --- AI Content Generation Helper ---
async def _generate_emails_for_contacts(user_id: str, user_name: str, contacts_in_campaign: List[dict], custom_prompt: str = "") -> List[dict]:
    """Helper function to generate email content using AI for a list of contacts."""
    if not GEMINI_API_KEY:
        logger.warning("AI generation skipped: GEMINI_API_KEY not configured.")
        # Return a structure indicating failure or default content
        return [{
            "contact_id": contact["_id"],
            "contact_name": contact.get("name", "N/A"),
            "generated_subject": "Subject Generation Skipped",
            "generated_body": "Body Generation Skipped - AI Not Configured.",
            "error": "AI Service not configured."
        } for contact in contacts_in_campaign]

    generated_emails = []
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        logger.info("Using Gemini model: gemini-1.5-flash for email generation.")

        for contact in contacts_in_campaign:
            contact_name = contact.get("name", "there")
            job_role_from_ref_tag = contact.get("ref_tag", "the specified position")
            contact_email = contact.get("email", "")
            contact_email_domain = contact_email.split('@')[-1] if '@' in contact_email else ""
            
            # Check if it's a personal email domain
            personal_email_domains = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "protonmail.com", "mail.com"]
            is_personal_email = any(domain in contact_email_domain.lower() for domain in personal_email_domains)
            
            if is_personal_email:
                # Use a more generic approach for personal emails
                company_reference = "at your company"
                inferred_company = contact.get("company_name", "your company")
            else:
                # For work emails, try to infer company from domain
                company_reference = f"at {contact_email_domain}"
                inferred_company = contact.get("company_name", "")
                if not inferred_company:
                    inferred_company = contact_email_domain.split('.')[0].capitalize() if '.' in contact_email_domain else contact_email_domain.capitalize()

            # Build the standard prompt
            standard_prompt = f"""
            Generate a professional and concise email subject and body for a job referral request.

            My details:
            - My Name: {user_name}

            Contact's details:
            - Contact Name: {contact_name}
            - I am asking for a referral for the job role: "{job_role_from_ref_tag}"
            - Company context: The contact works {company_reference}. {"Infer the company name as " + inferred_company if not is_personal_email else ""}

            Instructions for the email:
            1. Tone: Friendly but professional.
            2. Purpose: Ask for a job referral for the specified job role {"at their company" if is_personal_email else "at " + inferred_company}.
            3. Content:
                - Briefly state the purpose.
                - Express confidence in my fit for the role.
                - Mention that my resume will be attached.
                - Politely ask if they are willing and able to provide a referral.
            4. Subject Line: Generate a clear and concise subject line that includes my name and the job role.
            5. Body: Write the email body. Address the contact by their name. {"Don't assume the company name if not provided" if is_personal_email else "Refer to their company using the inferred company name."}
            """
            
            # Add custom prompt if provided
            final_prompt = standard_prompt
            if custom_prompt:
                final_prompt += f"\n\nAdditional instructions from the user: {custom_prompt}\n"
                logger.info(f"Adding custom prompt to email generation: {custom_prompt}")
            
            final_prompt += """
            Provide the output ONLY in the following JSON format, with no other text before or after the JSON block:
            {
              "subject": "Generated Subject Line Here",
              "body": "Generated email body content here..."
            }
            """

            response = await model.generate_content_async(final_prompt)
            response_text = response.text.strip()
            
            # Clean potential markdown code block fences
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            try:
                content = json.loads(response_text.strip())
                # Use job_role_from_ref_tag in fallback subject/body
                fallback_subject = f"Referral Request: {user_name} for {job_role_from_ref_tag}"
                
                if is_personal_email:
                    fallback_body = f"Hi {contact_name},\n\nI hope this email finds you well.\n\nI am writing to express my interest in the {job_role_from_ref_tag} position at your company and would be grateful if you would consider referring me.\n\nMy resume is attached for your review.\n\nThank you for your time and consideration.\n\nBest regards,\n{user_name}"
                else:
                    fallback_body = f"Hi {contact_name},\n\nI hope this email finds you well.\n\nI am writing to express my interest in the {job_role_from_ref_tag} position at {inferred_company} and would be grateful if you would consider referring me.\n\nMy resume is attached for your review.\n\nThank you for your time and consideration.\n\nBest regards,\n{user_name}"
                
                generated_emails.append({
                    "contact_id": contact["_id"],
                    "contact_name": contact_name,
                    "generated_subject": content.get("subject", fallback_subject),
                    "generated_body": content.get("body", fallback_body)
                })
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON from Gemini for contact {contact['_id']} (User: {user_id}): {response_text}")
                # Use job_role_from_ref_tag in fallback subject/body
                fallback_subject = f"Referral Request: {user_name} for {job_role_from_ref_tag}"
                
                if is_personal_email:
                    fallback_body = f"Hi {contact_name},\n\nI hope this email finds you well.\n\nI am writing to express my interest in the {job_role_from_ref_tag} position at your company and would be grateful if you would consider referring me.\n\nMy resume is attached for your review.\n\nThank you for your time and consideration.\n\nBest regards,\n{user_name}"
                else:
                    fallback_body = f"Hi {contact_name},\n\nI hope this email finds you well.\n\nI am writing to express my interest in the {job_role_from_ref_tag} position at {inferred_company} and would be grateful if you would consider referring me.\n\nMy resume is attached for your review.\n\nThank you for your time and consideration.\n\nBest regards,\n{user_name}"
                
                generated_emails.append({
                    "contact_id": contact["_id"],
                    "contact_name": contact_name,
                    "generated_subject": fallback_subject,
                    "generated_body": fallback_body,
                    "error": "AI generation failed to produce valid JSON."
                })
            except Exception as inner_e: # Catch other potential errors during processing
                 logger.error(f"Error processing Gemini response for contact {contact['_id']} (User: {user_id}): {inner_e}")
                 fallback_subject = f"Referral Request: {user_name} for {job_role_from_ref_tag}"
                 
                 if is_personal_email:
                    fallback_body = f"Hi {contact_name},\n\nI hope this email finds you well.\n\nI am writing to express my interest in the {job_role_from_ref_tag} position at your company and would be grateful if you would consider referring me.\n\nMy resume is attached for your review.\n\nThank you for your time and consideration.\n\nBest regards,\n{user_name}"
                 else:
                    fallback_body = f"Hi {contact_name},\n\nI hope this email finds you well.\n\nI am writing to express my interest in the {job_role_from_ref_tag} position at {inferred_company} and would be grateful if you would consider referring me.\n\nMy resume is attached for your review.\n\nThank you for your time and consideration.\n\nBest regards,\n{user_name}"
                    
                 generated_emails.append({
                    "contact_id": contact["_id"],
                    "contact_name": contact_name,
                    "generated_subject": fallback_subject,
                    "generated_body": fallback_body,
                    "error": f"Error processing AI response: {str(inner_e)}"
                })


    except Exception as e:
        logger.error(f"General error during AI email generation for user {user_id}: {e}")
        # Return error state for all contacts if the main generation loop fails
        return [{
            "contact_id": contact["_id"],
            "contact_name": contact.get("name", "N/A"),
            "generated_subject": "Subject Generation Failed",
            "generated_body": "Body Generation Failed.",
            "error": f"AI Service Error: {str(e)}"
        } for contact in contacts_in_campaign]
        
    return generated_emails


# --- AI Content Generation Route (Now uses the helper) ---

@app.post("/campaign/{campaign_id}/generate-all-emails", name="generate_campaign_emails")
async def generate_campaign_emails_route(request: Request, campaign_id: str):
    user_id = request.session.get("user_id")
    user_name = request.session.get("name")
    if not user_id or not user_name:
        raise HTTPException(status_code=401, detail="Not authenticated or user name not found in session.")

    try:
        # Parse request body to get custom prompt if provided
        request_data = await request.json()
        custom_prompt = request_data.get("custom_prompt", "")
        
        # Fetch campaign and contacts
        campaign = db.get_campaign_by_id(campaign_id, user_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found or access denied.")
        
        # Check if campaign has already been sent
        current_status = campaign.get("status", "draft")
        if current_status in ["sending", "sent"]:
            logger.warning(f"User {user_id} attempted to regenerate emails for campaign {campaign_id} which is already in status: {current_status}.")
            raise HTTPException(status_code=403, detail=f"Campaign has already been sent. Cannot regenerate emails for a campaign in '{current_status}' status.")
        
        contact_ids = campaign.get("contact_ids", [])
        if not contact_ids:
            return [] # Return empty list if no contacts

        contacts_in_campaign = db.get_contacts_by_ids(contact_ids, user_id)
        if not contacts_in_campaign:
             logger.warning(f"No contact details found for campaign {campaign_id} (User: {user_id}) despite having contact_ids.")
             return [] # Return empty list if contacts not found

        # Call the helper function to generate emails, passing the custom prompt
        generated_emails = await _generate_emails_for_contacts(user_id, user_name, contacts_in_campaign, custom_prompt)
        
        # Store the newly generated emails in the database
        if generated_emails:
            logger.info(f"Storing {len(generated_emails)} generated emails for campaign {campaign_id}")
            storage_success = db.store_draft_emails(campaign_id, user_id, generated_emails)
            if not storage_success:
                logger.warning(f"Failed to store generated emails for campaign {campaign_id}")
        else:
            logger.warning(f"No emails were generated for campaign {campaign_id}")
            
        # Return the generated emails to the client
        return generated_emails

    except HTTPException as http_exc:
        # Re-raise known HTTP exceptions
        raise http_exc
    except Exception as e:
        logger.error(f"Error in generate_campaign_emails_route for campaign {campaign_id} (User: {user_id}): {e}")
        # Raise a generic 500 error for unexpected issues
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while generating emails: {str(e)}")


# --- Email Sending Logic ---

# Helper function to send a single email
async def send_single_email(
    smtp_server: Optional[str] = None, 
    smtp_port: Optional[int] = None, 
    smtp_username: Optional[str] = None, 
    smtp_password: Optional[str] = None, 
    from_display_name: str = "", 
    recipient_email: str = "",
    recipient_name: str = "",
    subject: str = "",
    body_template: str = "", 
    resume_data: Optional[bytes] = None,
    resume_filename: Optional[str] = None,
    tracking_id: str = "",
    base_url: str = "", 
    user_id: str = "", 
    campaign_id: str = "",
    contact_id: str = "",
    job_role: str = ""
):
    try:
        # Fetch SMTP settings for the user
        smtp_settings = db.get_smtp_settings(user_id)
        if not smtp_settings:
            logger.error(f"SMTP settings not found for user {user_id}. Cannot send email to {recipient_email}.")
            # Optionally, log this failure to a specific campaign/user error log in DB
            return # Or raise an exception to be caught by the caller

        # Use fetched SMTP credentials
        smtp_server_to_use = smtp_settings.get("host")
        smtp_port_to_use = smtp_settings.get("port")
        smtp_username_to_use = smtp_settings.get("username") # This is the actual login username for SMTP
        smtp_password_to_use = smtp_settings.get("password")
        
        # Log the SMTP server and port (without exposing credentials)
        logger.info(f"Attempting to send email using SMTP server: {smtp_server_to_use}:{smtp_port_to_use}")
        
        # The "From" email address should also be the SMTP username in many cases, or a verified sender.
        # For simplicity, we'll use the SMTP username as the From: email address.
        from_email_address_to_use = smtp_username_to_use
        logger.info(f"Using sender email: {from_email_address_to_use}")

        # Personalize body
        template = Template(body_template)
        personalized_body = template.render(
            contact_name=recipient_name, 
            user_name=from_display_name, # Use the passed from_display_name
            job_role=job_role
        )

        # Create tracking pixel URL
        # Ensure base_url ends with a slash if needed, or construct carefully
        tracking_url = f"{base_url}track/{tracking_id}.png" # Use the passed base_url argument
        pixel_img_tag = f'<img src="{tracking_url}" width="1" height="1" alt="" style="display:none;">'
        
        # Append pixel to body (assuming HTML email)
        html_body = f"""
        <html>
            <body>
                {personalized_body.replace(chr(10), '<br>')} 
                {pixel_img_tag}
            </body>
        </html>
        """ # Basic HTML conversion, consider a proper HTML library if needed

        # Create email message
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{from_display_name} <{from_email_address_to_use}>"
        message["To"] = recipient_email
        
        logger.info(f"Sending email From: '{from_display_name} <{from_email_address_to_use}>' To: '{recipient_email}'")

        # Create tracking record BEFORE sending email
        # This ensures the record exists when the tracking pixel is fetched
        db.create_tracking_record(tracking_id, user_id, campaign_id, contact_id)
        logger.info(f"Created tracking record with ID: {tracking_id}")

        # Attach HTML body
        message.attach(MIMEText(html_body, "html"))

        # Attach resume if provided
        if resume_data and resume_filename:
            part = MIMEApplication(resume_data, Name=resume_filename)
            part['Content-Disposition'] = f'attachment; filename="{resume_filename}"'
            message.attach(part)
            logger.info(f"Attached resume: {resume_filename}")
        else:
            logger.warning(f"No resume data available to attach for email to {recipient_email}")

        # Send email
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server_to_use, smtp_port_to_use) as server:
            logger.info(f"Establishing connection to SMTP server {smtp_server_to_use}:{smtp_port_to_use}...")
            server.starttls(context=context)
            logger.info(f"TLS connection established, attempting login with username: {smtp_username_to_use}")
            server.login(smtp_username_to_use, smtp_password_to_use)
            logger.info(f"Login successful, sending email to {recipient_email}...")
            server.sendmail(smtp_username_to_use, recipient_email, message.as_string()) # Use smtp_username_to_use as from_addr
            logger.info(f"Email sent successfully to {recipient_email} for campaign {campaign_id} using user {user_id}'s SMTP.")

    except smtplib.SMTPAuthenticationError as auth_error:
        logger.error(f"SMTP Authentication failed for user {user_id} (SMTP user: {smtp_username_to_use}). Error: {str(auth_error)}")
        logger.error(f"Check credentials in SMTP settings. Make sure you're using an App Password if 2FA is enabled.")
        # Optionally: Log failure to DB or notify user
    except smtplib.SMTPRecipientsRefused as recip_error:
        logger.error(f"SMTP Error: Recipients refused for email to {recipient_email}: {str(recip_error)}")
    except smtplib.SMTPSenderRefused as sender_error:
        logger.error(f"SMTP Error: Sender address refused: {str(sender_error)}")
        logger.error(f"Make sure your SMTP username ({smtp_username_to_use}) is allowed to send from the From address.")
    except smtplib.SMTPException as e:
        logger.error(f"SMTP Error sending email to {recipient_email} for user {user_id}: {str(e)}")
        # Optionally: Log failure
    except Exception as e:
        logger.error(f"General Error sending email to {recipient_email}: {str(e)}", exc_info=True)
        # Optionally: Log failure


@app.post("/campaign/{campaign_id}/send", name="send_campaign")
async def send_campaign_route(
    request: Request, 
    campaign_id: str, 
    background_tasks: BackgroundTasks,
):
    user_id = request.session.get("user_id")
    user_display_name = request.session.get("name")

    if not user_id or not user_display_name:
        raise HTTPException(status_code=401, detail="Not authenticated or user name not found in session.")

    # Fetch user's email from DB
    user_details = db.get_user_by_id(user_id)
    if not user_details or "email" not in user_details:
        logger.error(f"Could not fetch email for user_id: {user_id}")
        request.session["flash_message"] = "Could not retrieve your email details to send the campaign."
        request.session["flash_category"] = "error"
        redirect_url = request.url_for("start_campaign_page", campaign_id=campaign_id) if campaign_id else request.url_for("campaigns_page")
        return RedirectResponse(url=redirect_url, status_code=303)
        
    user_actual_email = user_details["email"]

    # Check if user has SMTP settings configured
    smtp_settings = db.get_smtp_settings(user_id)
    if not smtp_settings:
        logger.warning(f"User {user_id} attempted to send campaign {campaign_id} but has no SMTP settings configured.")
        request.session["flash_message"] = "SMTP settings not configured. Please configure your SMTP details first."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("smtp_settings_page"), status_code=303)

    # These values won't be used since we fetch them again inside send_single_email, but we need to provide them
    # to satisfy the function signature
    smtp_host = smtp_settings.get("host", "")
    smtp_port = smtp_settings.get("port", 587)
    smtp_username = smtp_settings.get("username", "")
    smtp_password = smtp_settings.get("password", "")

    try:
        # 1. Fetch Campaign
        campaign = db.get_campaign_by_id(campaign_id, user_id)
        if not campaign:
            # This case is already handled by the HTTPException below, but good to be explicit
            request.session["flash_message"] = "Campaign not found or access denied."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

        # Check campaign status to prevent re-sending
        current_status = campaign.get("status", "draft") # Default to draft if status somehow missing
        if current_status in ["sending", "sent"]:
            logger.warning(f"User {user_id} attempt to resend campaign {campaign_id} which is already in status: {current_status}.")
            request.session["flash_message"] = f"Campaign '{campaign.get('campaign_name', '')}' has already been processed (status: {current_status})."
            request.session["flash_category"] = "warning"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

        # 2. Fetch Contacts for the campaign
        contact_ids = campaign.get("contact_ids", [])
        if not contact_ids:
             request.session["flash_message"] = "No contacts associated with this campaign."
             request.session["flash_category"] = "error"
             return RedirectResponse(url=request.url_for("start_campaign_page", campaign_id=campaign_id), status_code=303)
        
        contacts_in_campaign = db.get_contacts_by_ids(contact_ids, user_id)
        if not contacts_in_campaign:
             request.session["flash_message"] = "Could not retrieve contact details for this campaign."
             request.session["flash_category"] = "error"
             return RedirectResponse(url=request.url_for("start_campaign_page", campaign_id=campaign_id), status_code=303)

        # 3. Get resume data - fetch the first available resume
        user_resumes = db.get_resumes_for_user(user_id)
        if not user_resumes:
            request.session["flash_message"] = "No resume found. Please upload a resume first."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)
        
        # Get the resume data
        resume_id = user_resumes[0]["_id"]  # Use the first available resume
        resume_file_data = db.get_resume_file_data(str(resume_id), user_id)
        if not resume_file_data:
            logger.error(f"Failed to retrieve resume data for resume ID {resume_id}")
            request.session["flash_message"] = "Could not retrieve resume data. Please try again or upload a different resume."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("start_campaign_page", campaign_id=campaign_id), status_code=303)
        
        resume_bytes = resume_file_data["data_stream"]
        resume_filename = resume_file_data["filename"]
        logger.info(f"Successfully loaded resume {resume_filename} for campaign {campaign_id}")

        # Instead of relying on form data, get drafts directly from DB
        stored_drafts = db.get_draft_emails(campaign_id, user_id)
        if not stored_drafts:
            request.session["flash_message"] = "No draft emails found. Please generate emails first."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("start_campaign_page", campaign_id=campaign_id), status_code=303)
        
        # Create a map of contact_id to draft
        email_drafts_map = {draft["contact_id"]: draft for draft in stored_drafts}

        # 4. Add email sending tasks to background
        emails_queued_count = 0
        for contact in contacts_in_campaign:
            tracking_id = str(uuid.uuid4())
            contact_id = contact["_id"]
            recipient_email = contact["email"]
            recipient_name = contact.get("name", "there") # Default name
            
            # Get the draft from our map instead of form data
            email_draft = email_drafts_map.get(contact_id)
            if email_draft:
                # Create a task to send this email in the background
                background_tasks.add_task(
                    send_single_email,
                    smtp_server=smtp_host,
                    smtp_port=smtp_port,
                    smtp_username=smtp_username,
                    smtp_password=smtp_password,
                    from_display_name=user_display_name,
                    recipient_email=recipient_email,
                    recipient_name=recipient_name,
                    subject=email_draft.get("generated_subject", ""),
                    body_template=email_draft.get("generated_body", ""),
                    resume_data=resume_bytes,
                    resume_filename=resume_filename,
                    tracking_id=tracking_id,
                    base_url=PUBLIC_BASE_URL,  # Use the PUBLIC_BASE_URL from environment
                    user_id=user_id,
                    campaign_id=campaign_id,
                    contact_id=contact_id,
                    job_role=contact.get("ref_tag", "") # Pass along the contact's job role
                )
                emails_queued_count += 1
            else:
                logger.warning(f"No draft found for contact {contact_id} in database")

        logger.info(f"Campaign {campaign_id} sending initiated for {emails_queued_count} contacts by user {user_id} using their SMTP settings.")
        
        # Update campaign status to "sent"
        # (Consider "sending" if it's a very long process and you want an intermediate state)
        status_updated = db.update_campaign_status(campaign_id, user_id, "sent")
        if not status_updated:
            logger.error(f"Failed to update campaign {campaign_id} status to 'sent' for user {user_id} after queuing emails.")
            # Continue with success message for sending, but log this issue.
            # Optionally, add a specific flash message about status update failure.

        # Delete draft emails after successfully sending the campaign
        deleted_count = db.delete_draft_emails_for_campaign(campaign_id, user_id)
        logger.info(f"Deleted {deleted_count} draft emails for campaign {campaign_id} after sending.")

        request.session["flash_message"] = f"Campaign '{campaign['campaign_name']}' sending initiated for {emails_queued_count} contacts."
        request.session["flash_category"] = "success"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

    except HTTPException as http_exc:
        request.session["flash_message"] = http_exc.detail
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
        
    except Exception as e:
        logger.error(f"Error initiating campaign send for campaign {campaign_id}, user {user_id}: {e}")
        request.session["flash_message"] = "An unexpected error occurred while starting the campaign."
        request.session["flash_category"] = "error"
        # Redirect back to start page if possible, or campaigns page
        redirect_url = request.url_for("start_campaign_page", campaign_id=campaign_id) if campaign_id else request.url_for("campaigns_page")
        return RedirectResponse(url=redirect_url, status_code=303)

@app.get("/resumes", response_class=HTMLResponse, name="resumes_page")
async def resumes_page(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    flash_message = request.session.pop("flash_message", None)
    flash_category = request.session.pop("flash_category", None)
    
    try:
        user_resumes = db.get_resumes_for_user(user_id) # Assumes this function exists in database.py
    except Exception as e:
        logger.error(f"Error fetching resumes for user {user_id}: {e}")
        user_resumes = []
        flash_message = "Could not load your resumes at this time."
        flash_category = "error"

    return templates.TemplateResponse("resumes.html", {
        "request": request,
        "user_name": request.session.get("name"),
        "resumes": user_resumes,
        "flash_message": flash_message,
        "flash_category": flash_category
    })

@app.post("/upload-resume", name="upload_resume")
async def upload_resume_route(request: Request, resumeFile: UploadFile = File(...)):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Check resume count
    try:
        current_resume_count = db.count_resumes_for_user(user_id) # Assumes this function exists
        if current_resume_count >= MAX_RESUMES_PER_USER:
            request.session["flash_message"] = f"You can only upload a maximum of {MAX_RESUMES_PER_USER} resumes."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)
    except Exception as e:
        logger.error(f"Error counting resumes for user {user_id}: {e}")
        request.session["flash_message"] = "Could not verify your current resume count. Please try again."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)

    # Validate file type
    if resumeFile.content_type != ALLOWED_RESUME_CONTENT_TYPE:
        request.session["flash_message"] = "Invalid file type. Only PDF files are allowed."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)

    # Validate file size
    contents = await resumeFile.read()
    if len(contents) > MAX_RESUME_SIZE_BYTES:
        request.session["flash_message"] = f"File too large. Maximum size is {MAX_RESUME_SIZE_MB}MB."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)
    
    await resumeFile.seek(0) # Reset file pointer after reading

    try:
        # filename, content_type, file_data, user_id
        # db.save_resume should handle GridFS storage and metadata in 'docs' or 'resumes_metadata' collection
        result = db.save_resume(
            user_id=user_id,
            filename=resumeFile.filename,
            content_type=resumeFile.content_type,
            file_data=contents # Pass contents directly
        )
        if result.get("success"):
            request.session["flash_message"] = "Resume uploaded successfully."
            request.session["flash_category"] = "success"
        else:
            request.session["flash_message"] = result.get("message", "Failed to upload resume.")
            request.session["flash_category"] = "error"
    except Exception as e:
        logger.error(f"Error saving resume for user {user_id}: {e}")
        request.session["flash_message"] = "An unexpected error occurred while saving your resume."
        request.session["flash_category"] = "error"
    
    return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)

@app.get("/view-resume/{resume_id}", name="view_resume")
async def view_resume_route(request: Request, resume_id: str):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        # Validate resume_id format if it's an ObjectId
        # if not ObjectId.is_valid(resume_id):
        #     raise HTTPException(status_code=400, detail="Invalid resume ID format.")
        
        # db.get_resume_file_data should return a dict with 'filename', 'content_type', and 'data_stream' (BytesIO) or 'file_path'
        resume_data = db.get_resume_file_data(resume_id, user_id) 
        if not resume_data:
            raise HTTPException(status_code=404, detail="Resume not found or you do not have permission to view it.")

        # If db.get_resume_file_data returns a path to a temporary file:
        # return FileResponse(resume_data["file_path"], media_type=resume_data["content_type"], filename=resume_data["filename"])
        
        # If db.get_resume_file_data returns a BytesIO stream (which it does as bytes):
        return StreamingResponse(
            io.BytesIO(resume_data["data_stream"]), # data_stream is bytes, wrap in BytesIO
            media_type=resume_data["content_type"],
            headers={"Content-Disposition": f"inline; filename=\"{resume_data['filename']}\""}
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error retrieving resume {resume_id} for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve resume.")

@app.post("/delete-resume/{resume_id}", name="delete_resume")
async def delete_resume_route(request: Request, resume_id: str):
    user_id = request.session.get("user_id")
    if not user_id:
        # This should ideally be a 401, but forms might not handle non-redirects well.
        # For simplicity with HTML forms, redirect to login.
        # A better approach for APIs is to return 401.
        return RedirectResponse(url="/login", status_code=302)

    try:
        # if not ObjectId.is_valid(resume_id):
        #     request.session["flash_message"] = "Invalid resume ID."
        #     request.session["flash_category"] = "error"
        #     return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)

        deleted = db.delete_resume_for_user(resume_id, user_id) # Assumes this function exists
        if deleted:
            request.session["flash_message"] = "Resume deleted successfully."
            request.session["flash_category"] = "success"
        else:
            request.session["flash_message"] = "Failed to delete resume. It might have already been deleted or you don't have permission."
            request.session["flash_category"] = "error"
    except Exception as e:
        logger.error(f"Error deleting resume {resume_id} for user {user_id}: {e}")
        request.session["flash_message"] = "An unexpected error occurred while deleting the resume."
        request.session["flash_category"] = "error"
    
    return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"): # If already logged in, redirect to home
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


# Registration routes removed
# @app.get("/register", response_class=HTMLResponse)
# async def register_page(request: Request):
#     if request.session.get("user_id"): # If already logged in, redirect to home
#         return RedirectResponse(url="/", status_code=302)
#     return templates.TemplateResponse("register.html", {"request": request})

@app.post("/search")
async def search_emails(
    request: Request, # Added request to access session
    domain: str = Form(...),
    department: Optional[str] = Form(None),
    seniority: Optional[str] = Form(None),
    name: Optional[str] = Form(None)
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        logger.info(f"Searching emails for domain: {domain} by user {user_id}")
        
        url = 'https://api.hunter.io/v2/domain-search'
        params = {
            'domain': domain,
            'api_key': HUNTER_API_KEY,
            'limit': 10
        }
        if department:
            params['department'] = department
        if seniority:
            params['seniority'] = seniority
        logger.info(f"Requesting: {url} with params {params}")
        response = requests.get(url, params=params)
        logger.info(f"Hunter.io response status: {response.status_code}")
        logger.info(f"Hunter.io response text: {response.text}")
        if response.status_code != 200:
            try:
                error_message = response.json().get('errors', [{}])[0].get('details', response.text)
            except Exception:
                error_message = response.text
            logger.error(f"Hunter.io API error: {error_message}")
            raise HTTPException(
                status_code=500,
                detail=f"Hunter.io API error: {error_message}"
            )
        data = response.json()
        emails = data.get('data', {}).get('emails', [])
        if not emails:
            logger.info(f"No email data found for domain {domain}")
            return {
                "results": [],
                "domain_info": {
                    "organization": data.get('data', {}).get('organization'),
                    "pattern": data.get('data', {}).get('pattern'),
                    "disposable": data.get('data', {}).get('disposable'),
                    "webmail": data.get('data', {}).get('webmail')
                },
                "message": f"No emails found for domain {domain}."
            }
        results = []
        for person in emails:
            if name:
                full_name = f"{person.get('first_name', '')} {person.get('last_name', '')}".lower()
                if name.lower() not in full_name:
                    continue
            contact = {
                "name": f"{person.get('first_name', '')} {person.get('last_name', '')}",
                "email": person.get('value'),
                "position": person.get('position'),
                "department": person.get('department'),
                "seniority": person.get('seniority'),
                "confidence": person.get('confidence'),
                "company_name": domain.split(".")[0].replace("-", " ").title()
            }
            results.append(contact)
            logger.info(f"Added contact: {contact['name']} ({contact['email']})")
        logger.info(f"Search completed. Found {len(results)} contacts")
        return {
            "results": results,
            "domain_info": {
                "organization": data.get('data', {}).get('organization'),
                "pattern": data.get('data', {}).get('pattern'),
                "disposable": data.get('data', {}).get('disposable'),
                "webmail": data.get('data', {}).get('webmail')
            },
            "message": f"Found {len(results)} emails for {domain}."
        }
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error searching emails: {str(e)}"
        )

@app.post("/save-contacts")
async def save_contacts(request: Request, contacts: List[dict]): # Added request
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Add user_id to each contact before saving
        contacts_with_user_id = [{**contact, "user_id": user_id} for contact in contacts]
        result = db.save_contacts(contacts_with_user_id)
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["message"])
        return {"message": result["message"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/contacts")
async def get_contacts(request: Request): # Changed user_id param to request
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        contacts = db.get_contacts(user_id) # Pass session user_id
        return {"contacts": contacts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/contacts/add")
async def add_manual_contact(request: Request, contact_data: dict):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Ensure required fields are present (name and email for a basic contact)
    if not contact_data.get("name") or not contact_data.get("email"):
        raise HTTPException(status_code=400, detail="Name and Email are required for manual contact.")

    try:
        # Prepare the contact object, including the user_id
        contact_to_save = {
            "name": contact_data.get("name"),
            "email": contact_data.get("email"),
            "company_name": contact_data.get("company_name"),
            "position": contact_data.get("position"),
            "department": contact_data.get("department"),
            "seniority": contact_data.get("seniority"),
            "ref_tag": contact_data.get("ref_tag"), # Added ref_tag
            "user_id": user_id,
            "source": "manual" # Indicate it's a manually added contact
        }
        
        # db.save_contacts expects a list of contacts
        result = db.save_contacts([contact_to_save]) 
        
        if not result["success"]:
            # If db.save_contacts itself has an issue, it returns success:False
            # and a message. We can use that or a generic one.
            logger.error(f"Failed to save manual contact for user {user_id}: {result.get('message')}")
            raise HTTPException(status_code=500, detail=result.get("message", "Failed to save manually added contact."))
        
        return {"message": "Contact added successfully!"}
    except HTTPException as http_exc:
        raise http_exc # Re-raise if it's already an HTTPException
    except Exception as e:
        logger.error(f"Error adding manual contact for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.delete("/contacts/delete/{contact_id}")
async def delete_contact_by_id(request: Request, contact_id: str):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        # Optional: Verify that the contact belongs to the user trying to delete it.
        # contact_to_delete = db.contacts.find_one({"_id": ObjectId(contact_id), "user_id": user_id}) # Requires ObjectId from bson
        # if not contact_to_delete:
        #     raise HTTPException(status_code=404, detail="Contact not found or you don't have permission to delete it.")
        
        # For now, directly attempt deletion. db.delete_contact uses string contact_id.
        result = db.delete_contact(contact_id)
        
        if not result["success"]:
            # Handle cases like contact not found, or other DB errors from delete_contact
            status_code = 404 if "not found" in result.get("message", "").lower() else 500
            raise HTTPException(status_code=status_code, detail=result.get("message", "Failed to delete contact."))
        
        return {"message": "Contact deleted successfully"}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error deleting contact {contact_id} for user {user_id}: {str(e)}")
        # Be careful not to expose too much detail from generic exceptions.
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while deleting the contact.")

from pydantic import BaseModel
class RefTagUpdate(BaseModel):
    ref_tag: Optional[str] = None

@app.put("/contacts/update-tag/{contact_id}")
async def update_contact_tag_endpoint(request: Request, contact_id: str, payload: RefTagUpdate):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Optional: Add verification here to ensure the contact_id belongs to the user_id if necessary for security.
    # For example:
    # contact = db.contacts.find_one({"_id": ObjectId(contact_id), "user_id": user_id})
    # if not contact:
    #     raise HTTPException(status_code=404, detail="Contact not found or access denied.")

    try:
        result = db.update_contact_ref_tag(contact_id, payload.ref_tag)
        if not result["success"]:
            status_code = 404 if "not found" in result.get("message", "").lower() else 500
            raise HTTPException(status_code=status_code, detail=result.get("message", "Failed to update reference tag."))
        return result # Contains success message
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error updating ref_tag for contact {contact_id} by user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred while updating the tag.")

# Registration POST route removed
# @app.post("/register")
# async def register_user(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...)):
#     try:
#         result = db.create_user(name, email, password)
#         if not result["success"]:
#             raise HTTPException(status_code=400, detail=result["message"])
#         # Optionally log the user in directly after registration
#         # request.session["user_id"] = result["user_id"]
#         # request.session["name"] = name
#         # return RedirectResponse(url="/", status_code=302) 
#         return {"message": result["message"], "user_id": result["user_id"]} # Keep JSON response for now, frontend handles redirect or message
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        result = db.verify_user(email, password)
        if not result["success"]:
            # Return a JSON response for the frontend to handle the error message
            raise HTTPException(status_code=401, detail=result["message"])
        
        request.session["user_id"] = result["user_id"]
        request.session["name"] = result["name"]
        # On successful login, redirect to the home page.
        # The frontend JavaScript will see the redirect and reload the page.
        return RedirectResponse(url="/", status_code=302)
    except HTTPException as http_exc: # Re-raise HTTPException to send JSON error
        raise http_exc
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during login: {str(e)}")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

@app.post("/campaign/{campaign_id}/send-now", name="send_campaign_now")
async def send_campaign_now_route(
    request: Request,
    campaign_id: str,
    background_tasks: BackgroundTasks
):
    user_id = request.session.get("user_id")
    user_display_name = request.session.get("name") # User's display name

    if not user_id or not user_display_name:
        request.session["flash_message"] = "Authentication required."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

    # Fetch user's email from DB
    user_details = db.get_user_by_id(user_id)
    if not user_details or "email" not in user_details:
        logger.error(f"Could not fetch email for user_id: {user_id} in send_campaign_now_route")
        request.session["flash_message"] = "Could not retrieve your email details to send the campaign."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
        
    user_actual_email = user_details["email"]

    # Check if user has SMTP settings configured
    smtp_settings = db.get_smtp_settings(user_id)
    if not smtp_settings:
        logger.warning(f"User {user_id} attempted to 'send now' campaign {campaign_id} but has no SMTP settings configured.")
        request.session["flash_message"] = "SMTP settings not configured. Please configure your SMTP details first."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("smtp_settings_page"), status_code=303)

    # These values won't be used since we fetch them again inside send_single_email, but we need to provide them
    # to satisfy the function signature
    smtp_host = smtp_settings.get("host", "")
    smtp_port = smtp_settings.get("port", 587)
    smtp_username = smtp_settings.get("username", "")
    smtp_password = smtp_settings.get("password", "")

    try:
        # 1. Fetch Campaign
        campaign = db.get_campaign_by_id(campaign_id, user_id)
        if not campaign:
            request.session["flash_message"] = "Campaign not found or access denied."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

        # Check campaign status to prevent re-sending
        current_status = campaign.get("status", "draft") # Default to draft if status somehow missing
        if current_status in ["sending", "sent"]:
            logger.warning(f"User {user_id} attempt to 'send now' campaign {campaign_id} which is already in status: {current_status}.")
            request.session["flash_message"] = f"Campaign '{campaign.get('campaign_name', '')}' has already been processed (status: {current_status})."
            request.session["flash_category"] = "warning"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

        # 2. Fetch Contacts for the campaign
        contact_ids = campaign.get("contact_ids", [])
        if not contact_ids:
            request.session["flash_message"] = "No contacts associated with this campaign."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
        
        contacts_in_campaign = db.get_contacts_by_ids(contact_ids, user_id)
        if not contacts_in_campaign:
            request.session["flash_message"] = "Could not retrieve contact details for this campaign."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

        # 3. Fetch the first available resume for the user
        user_resumes = db.get_resumes_for_user(user_id)
        if not user_resumes:
            request.session["flash_message"] = "No resume found. Please upload a resume first."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("resumes_page"), status_code=303)
        
        first_resume_id = user_resumes[0]["_id"] # Assuming _id is the ID field
        resume_file_data = db.get_resume_file_data(str(first_resume_id), user_id) # Ensure ID is string

        if not resume_file_data:
            request.session["flash_message"] = "Could not retrieve resume data for the first resume."
            request.session["flash_category"] = "error"
            return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
            
        resume_bytes = resume_file_data["data_stream"]
        resume_filename = resume_file_data["filename"]

        # 4. Get stored draft emails or generate them if they don't exist
        stored_drafts = db.get_draft_emails(campaign_id, user_id)
        
        # If no stored drafts exist, generate them now
        if not stored_drafts:
            logger.info(f"No stored draft emails found for campaign {campaign_id}. Generating new content.")
            generated_email_contents = await _generate_emails_for_contacts(user_id, user_display_name, contacts_in_campaign)
            
            # Store the newly generated emails
            if generated_email_contents:
                db.store_draft_emails(campaign_id, user_id, generated_email_contents)
                stored_drafts = generated_email_contents
            else:
                request.session["flash_message"] = "Failed to generate email content. Please try again."
                request.session["flash_category"] = "error"
                return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
        else:
            logger.info(f"Using {len(stored_drafts)} stored draft emails for campaign {campaign_id}")
        
        # Create a dictionary for quick lookup of generated content by contact_id
        email_content_map = {draft["contact_id"]: draft for draft in stored_drafts}

        # 5. Add email sending tasks to background
        emails_queued_count = 0
        for contact in contacts_in_campaign:
            tracking_id = str(uuid.uuid4())
            contact_id_str = str(contact["_id"]) # Ensure contact_id is a string for map lookup
            recipient_email = contact["email"]
            recipient_name = contact.get("name", "there")
            job_role = contact.get("ref_tag", "the position")

            # Get the stored subject and body for this contact
            draft_content = email_content_map.get(contact_id_str)
            if not draft_content:
                logger.warning(f"No draft email content found for contact {contact_id_str} in campaign {campaign_id}. Using defaults.")
                email_subject = f"Referral Request: {user_display_name} for {job_role}"
                email_body = f"Hi {recipient_name},\n\nPlease find my resume attached for the {job_role} position.\n\nBest regards,\n{user_display_name}"
            else:
                email_subject = draft_content.get("generated_subject", f"Referral Request: {user_display_name} for {job_role}")
                email_body = draft_content.get("generated_body", f"Hi {recipient_name},\n\nPlease find my resume attached for the {job_role} position.\n\nBest regards,\n{user_display_name}")

            background_tasks.add_task(
                send_single_email,
                smtp_server=smtp_host,
                smtp_port=smtp_port,
                smtp_username=smtp_username,
                smtp_password=smtp_password,
                from_display_name=user_display_name,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                subject=email_subject,
                body_template=email_body,
                resume_data=resume_bytes,
                resume_filename=resume_filename,
                tracking_id=tracking_id,
                base_url=str(request.base_url),
                user_id=user_id, # Pass the user_id for whom the email is being sent
                campaign_id=campaign_id,
                contact_id=contact_id_str,
                job_role=job_role
            )
            emails_queued_count += 1

        logger.info(f"Direct send for campaign {campaign_id} initiated for {emails_queued_count} contacts by user {user_id} using their SMTP settings.")
        
        # Update campaign status to "sent"
        status_updated = db.update_campaign_status(campaign_id, user_id, "sent")
        if not status_updated:
            logger.error(f"Failed to update campaign {campaign_id} status to 'sent' for user {user_id} after 'send now' queuing.")
            # Continue with success message for sending, but log this issue.

        # Delete draft emails after successfully sending the campaign
        deleted_count = db.delete_draft_emails_for_campaign(campaign_id, user_id)
        logger.info(f"Deleted {deleted_count} draft emails for campaign {campaign_id} after send-now.")

        request.session["flash_message"] = f"Campaign '{campaign['campaign_name']}' sending initiated for {emails_queued_count} contacts using your first resume."
        request.session["flash_category"] = "success"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

    except HTTPException as http_exc:
        request.session["flash_message"] = http_exc.detail
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)
        
    except Exception as e:
        logger.error(f"Error in send_campaign_now_route for campaign {campaign_id}, user {user_id}: {e}")
        request.session["flash_message"] = f"An unexpected error occurred: {str(e)}"
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

# Tracking Pixel Endpoint
@app.get("/track/{tracking_id}.png", name="track_email_open")
async def track_email_open_route(tracking_id: str, request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint that serves a 1x1 transparent PNG image and records email opens.
    The tracking_id in the URL uniquely identifies the email send.
    This is called automatically when an email with a tracking pixel is opened.
    """
    try:
        # Get and log user agent to help with debugging
        user_agent = request.headers.get("User-Agent", "")
        logger.info(f"Tracking pixel accessed for tracking_id: {tracking_id}, UA: {user_agent}")
        
        # List of known bot crawlers to ignore
        known_bots = [
            "Googlebot",
            "Bingbot",
            "Baiduspider",
            "facebookexternalhit",
            "Slackbot",
            "WhatsApp",
            "Edge/12"
        ]
        
        # Only count as an open if not from a known proxy agent
        is_bot = any(bot.lower() in user_agent.lower() for bot in known_bots)
        if not is_bot:
            logger.info(f"Counting open for {tracking_id} (real user agent)")
            background_tasks.add_task(db.mark_email_as_opened, tracking_id)
        else:
            logger.info(f"Skipping counting open for {tracking_id} (detected bot: {user_agent})")
        
        # Return a 1x1 transparent PNG image
        # Pixel data (base64 encoded 1x1 transparent PNG)
        pixel_data_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        import base64
        pixel_data = base64.b64decode(pixel_data_base64)
        
        # Set cache control headers to prevent caching
        return Response(content=pixel_data, media_type="image/png", headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        })
    except Exception as e:
        logger.error(f"Error processing tracking pixel for {tracking_id}: {str(e)}")
        # Still return a transparent pixel even if an error occurs to avoid breaking email clients
        pixel_data_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        import base64
        pixel_data = base64.b64decode(pixel_data_base64)
        return Response(content=pixel_data, media_type="image/png")

@app.get("/email-tracking", response_class=HTMLResponse, name="email_tracking_dashboard")
@login_required
async def email_tracking_dashboard(request: Request):
    """Dashboard to view email tracking statistics for all campaigns"""
    user_id = request.session.get("user_id")
    
    try:
        # Get tracking stats for all campaigns
        tracking_stats = db.get_all_tracking_stats(user_id)
        
        # Get flash messages if any
        flash_message = request.session.pop("flash_message", None)
        flash_category = request.session.pop("flash_category", "info")
        
        return templates.TemplateResponse("email_tracking.html", {
            "request": request,
            "user_name": request.session.get("name"),
            "tracking_stats": tracking_stats,
            "flash_message": flash_message,
            "flash_category": flash_category
        })
    except Exception as e:
        logger.error(f"Error loading email tracking dashboard for user {user_id}: {str(e)}")
        # Handle the error
        request.session["flash_message"] = "An error occurred while loading tracking statistics."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

@app.get("/email-tracking/{campaign_id}", response_class=HTMLResponse, name="campaign_tracking_details")
@login_required
async def campaign_tracking_details(request: Request, campaign_id: str):
    """Detailed tracking statistics for a single campaign"""
    user_id = request.session.get("user_id")
    
    try:
        # Get tracking stats for this campaign
        tracking_stats = db.get_tracking_stats_for_campaign(campaign_id, user_id)
        
        # Get campaign details
        campaign = db.get_campaign_by_id(campaign_id, user_id)
        
        # Get contact details for the tracked emails
        contact_ids = [record.get("contact_id") for record in tracking_stats.get("tracking_records", [])]
        contacts = db.get_contacts_by_ids(contact_ids, user_id)
        
        # Create a map of contact_id to contact info for quick lookup
        contact_map = {contact["_id"]: contact for contact in contacts}
        
        # Add contact info to tracking records
        for record in tracking_stats.get("tracking_records", []):
            contact_id = record.get("contact_id")
            if contact_id in contact_map:
                record["contact_name"] = contact_map[contact_id].get("name", "Unknown")
                record["contact_email"] = contact_map[contact_id].get("email", "Unknown")
            else:
                record["contact_name"] = "Unknown"
                record["contact_email"] = "Unknown"
        
        # Get flash messages if any
        flash_message = request.session.pop("flash_message", None)
        flash_category = request.session.pop("flash_category", "info")
        
        return templates.TemplateResponse("campaign_tracking.html", {
            "request": request,
            "user_name": request.session.get("name"),
            "tracking_stats": tracking_stats,
            "campaign": campaign,
            "flash_message": flash_message,
            "flash_category": flash_category
        })
    except Exception as e:
        logger.error(f"Error loading tracking details for campaign {campaign_id}, user {user_id}: {str(e)}")
        # Handle the error
        request.session["flash_message"] = "An error occurred while loading campaign tracking details."
        request.session["flash_category"] = "error"
        return RedirectResponse(url=request.url_for("campaigns_page"), status_code=303)

# --- SMTP Settings Routes ---
@app.get("/smtp-settings", response_class=HTMLResponse, name="smtp_settings_page")
@login_required
async def smtp_settings_page(request: Request):
    user_id = request.session.get("user_id")
    
    smtp_settings = db.get_smtp_settings(user_id) # Fetch current settings if they exist
    
    flash_message = request.session.pop("flash_message", None)
    flash_category = request.session.pop("flash_category", "info") # Default category to info

    messages = []
    if flash_message:
        messages.append((flash_category, flash_message))

    return templates.TemplateResponse("smtp.html", {
        "request": request,
        "user_name": request.session.get("name"),
        "smtp_settings": smtp_settings, # Pass current settings to the template
        "messages": messages # Pass the list of messages
    })

@app.post("/smtp-settings", name="save_smtp_settings")
@login_required
async def save_smtp_settings_route(
    request: Request,
    smtp_host: str = Form(...),
    smtp_port: int = Form(...),
    smtp_username: str = Form(...),
    smtp_password: str = Form(...) # Password will be stored as is. Consider encryption.
):
    user_id = request.session.get("user_id")
    
    settings_data = {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password # Storing password in plain text is a security risk.
                                      # Consider encrypting it before saving to DB.
    }
    
    result = db.save_or_update_smtp_settings(user_id, settings_data)
    
    if result.get("success"):
        request.session["flash_message"] = result.get("message", "SMTP settings saved successfully!")
        request.session["flash_category"] = "success"
    else:
        request.session["flash_message"] = result.get("message", "Failed to save SMTP settings.")
        request.session["flash_category"] = "error"
        
    return RedirectResponse(url=request.url_for("smtp_settings_page"), status_code=303)

@app.get("/debug/test-email-send", name="test_email_send")
@login_required
async def test_email_send(request: Request, background_tasks: BackgroundTasks):
    """Debug endpoint to test email sending directly"""
    user_id = request.session.get("user_id")
    user_display_name = request.session.get("name")
    
    # Log start of test
    logger.info(f"Starting test email send for user {user_id} ({user_display_name})")
    
    # Check if SMTP settings are configured
    smtp_settings = db.get_smtp_settings(user_id)
    if not smtp_settings:
        logger.error(f"No SMTP settings found for user {user_id}")
        return {"status": "error", "message": "No SMTP settings configured. Please set up SMTP first."}
    
    # Get SMTP settings to pass to the email function
    smtp_host = smtp_settings.get("host", "")
    smtp_port = smtp_settings.get("port", 587)
    smtp_username = smtp_settings.get("username", "")
    smtp_password = smtp_settings.get("password", "")
    
    # Create test body and subject
    test_subject = f"Test Email from {user_display_name}"
    test_body = f"""
    <p>This is a test email sent from the campaign email system.</p>
    <p>If you're seeing this, the email sending functionality is working!</p>
    <p>Best regards,<br>{user_display_name}</p>
    """
    
    # Try to send the test email
    try:
        # We'll send to the user's own email
        user_details = db.get_user_by_id(user_id)
        if not user_details or "email" not in user_details:
            logger.error(f"Could not fetch email for user_id: {user_id}")
            return {"status": "error", "message": "Could not retrieve your email address."}
        
        recipient_email = user_details["email"]
        tracking_id = str(uuid.uuid4())
        
        # Send email in background so the response is not delayed
        background_tasks.add_task(
            send_single_email,
            smtp_server=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            from_display_name=user_display_name,
            recipient_email=recipient_email,
            recipient_name=user_display_name,
            subject=test_subject,
            body_template=test_body,
            resume_data=None,  # No resume for test
            resume_filename=None,
            tracking_id=tracking_id,
            base_url=str(request.base_url),
            user_id=user_id,
            campaign_id="test",
            contact_id="test",
            job_role="Test"
        )
        
        logger.info(f"Test email queued for sending to {recipient_email}")
        return {
            "status": "success", 
            "message": f"Test email queued for sending to {recipient_email}. Check your inbox and the log file for details."
        }
        
    except Exception as e:
        logger.error(f"Error in test email send: {str(e)}", exc_info=True)
        return {"status": "error", "message": f"An error occurred: {str(e)}"}

@app.post("/campaign/{campaign_id}/update-draft", name="update_campaign_draft")
async def update_campaign_draft(request: Request, campaign_id: str):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Parse request data
        data = await request.json()
        contact_id = data.get("contact_id")
        subject = data.get("subject")
        body = data.get("body")
        resume_id = data.get("resume_id")
        
        if not contact_id or not subject or not body:
            raise HTTPException(status_code=400, detail="Missing required fields (contact_id, subject, body)")
        
        # Validate that the campaign belongs to the user
        campaign = db.get_campaign_by_id(campaign_id, user_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found or access denied")
        
        # Validate that the campaign isn't already sent
        if campaign.get("status") in ["sending", "sent"]:
            raise HTTPException(status_code=400, detail="Cannot update drafts for campaigns that are already sent")
        
        # Update the draft in the database
        success = db.update_draft_email(campaign_id, user_id, contact_id, subject, body, resume_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update draft")
        
        return {"status": "success", "message": "Draft updated successfully"}
        
    except HTTPException as he:
        # Re-throw HTTP exceptions
        raise he
    except Exception as e:
        logger.error(f"Error updating draft for campaign {campaign_id}, user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@app.post("/campaign/{campaign_id}/generate-single-email", name="generate_single_email")
async def generate_single_email(request: Request, campaign_id: str):
    user_id = request.session.get("user_id")
    user_name = request.session.get("name")
    if not user_id or not user_name:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Parse request data
        data = await request.json()
        contact_id = data.get("contact_id")
        custom_prompt = data.get("custom_prompt", "")
        
        if not contact_id:
            raise HTTPException(status_code=400, detail="Missing contact_id")
        
        # Validate that the campaign belongs to the user
        campaign = db.get_campaign_by_id(campaign_id, user_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found or access denied")
        
        # Validate that the campaign isn't already sent
        if campaign.get("status") in ["sending", "sent"]:
            raise HTTPException(status_code=400, detail="Cannot generate emails for campaigns that are already sent")
        
        # Get the contact details
        contact = db.get_contact_by_id(contact_id, user_id)
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found or access denied")
        
        # Generate email for this single contact
        generated_emails = await _generate_emails_for_contacts(user_id, user_name, [contact], custom_prompt)
        
        if not generated_emails or len(generated_emails) == 0:
            raise HTTPException(status_code=500, detail="Failed to generate email content")
        
        generated_email = generated_emails[0]
        
        # Update the draft in the database with the newly generated content
        success = db.update_draft_email(
            campaign_id, 
            user_id, 
            contact_id, 
            generated_email.get("generated_subject", ""), 
            generated_email.get("generated_body", "")
        )
        
        if not success:
            logger.warning(f"Failed to store updated draft for contact {contact_id} in campaign {campaign_id}")
        
        return generated_email
        
    except HTTPException as he:
        # Re-throw HTTP exceptions
        raise he
    except Exception as e:
        logger.error(f"Error generating email for contact in campaign {campaign_id}, user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    # Ensure database module is correctly set up before running
    # For example, db.init_db() if you have an initialization function
    uvicorn.run(app, host="0.0.0.0", port=8000)
