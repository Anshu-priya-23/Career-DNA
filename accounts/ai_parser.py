import fitz  # PyMuPDF
import google.generativeai as genai
import json

# Replace with your actual API key
load_dotenv()
API_KEY = os.environ["GCP_API_KEY"]
genai.configure(api_key=API_KEY)



def _clean_json_response(raw_text):
    """
    Helper function to clean markdown and fix malformed JSON from Gemini.
    Ensures the response has opening and closing curly braces.
    """
    text = raw_text.strip()
    
    # Remove markdown code blocks if present
    text = text.replace('```json', '').replace('```', '').strip()
    
    # Force the curly braces if Gemini returned raw key-value pairs without them
    if not text.startswith('{'):
        text = '{' + text
    if not text.endswith('}'):
        text = text + '}'
        
    return text

def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    return text

def parse_resume_with_ai(pdf_path):
    raw_text = extract_text_from_pdf(pdf_path)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    prompt = f"""
    Extract the following information from this resume text and return ONLY valid JSON:
    {{
        "skills": ["skill1", "skill2"],
        "summary": "short summary",
        "experience_years": 0
    }}
    Resume Text: {raw_text}
    """
    
    try:
        response = model.generate_content(prompt)
        cleaned_json = _clean_json_response(response.text)
        return json.loads(cleaned_json)
    except Exception as e:
        print(f"Resume Parsing Error: {e}")
        return {"skills": [], "summary": "", "experience_years": 0}

def analyze_skill_gap(current_skills, dream_role, dream_company):
    """Compares current skills against a target role and returns structured roadmap."""

    if not current_skills or not dream_role or not dream_company:
        raise ValueError("Missing data: Need skills, role, and company to analyze.")

    model = genai.GenerativeModel('gemini-2.5-flash')

    prompt = f"""
You are an expert career mentor.

A student has the following skills:

{current_skills}

Their dream role is:
{dream_role}

Their dream company is:
{dream_company}

Analyze the skill gap between the student's current skills and the expected skills for this role.

Return ONLY a valid JSON object.

Format:

{{
    "missing_skills": "Comma separated list of missing skills",
    "resume_score": "A+",
    ""roadmap": [
    {{"task": "Learn Docker", "video_link": "https://youtube.com/watch?v=..."}},
    {{"task": "Build Backend API", "video_link": "https://youtube.com/watch?v=..."}}
]
}}

Rules:
- Return ONLY JSON.
- No markdown.
- No explanation.
- Resume score must be one of:
A+, A, B, C, D.
- Generate 5-10 actionable study steps.
"""

    try:
        response = model.generate_content(prompt)

        print("DEBUG AI RESPONSE:")
        print(response.text)

        cleaned_json = _clean_json_response(response.text)

        return json.loads(cleaned_json)

    except Exception as e:
        print(f"Gap Analysis Error: {e}")

        return {
            "missing_skills": "",
            "resume_score": "N/A",
            "study_plan_steps": []
        }