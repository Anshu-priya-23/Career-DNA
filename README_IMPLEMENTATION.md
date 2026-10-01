# CareerDNA

The existing Django application provides a resume review followed by an optional daily learning checklist. Login, signup, profile data, saved resumes and existing progress are preserved. No database schema changes are required for this redesign.

## Run (PowerShell)

```powershell
Set-Location C:\CareerDNA
.\env\Scripts\python.exe manage.py migrate
.\env\Scripts\python.exe manage.py runserver 8001
```

Open http://127.0.0.1:8001/ and sign in. Upload a resume PDF, enter a role and paste the job description. After reviewing the suggestions, choose **Yes, create my plan**, tick skills you already know, and enter days available and study hours per day.

Review and plan failures preserve previous results. The Gemini daily quota and transient provider fallback remain enabled. Diagnostics log exception types/statuses without resume text, keys or provider response bodies in `gemini-diagnostics.log` (ignored by Git).

The plan follows prerequisites and defers whole topics that do not fit the time budget. Completion checkboxes save immediately and survive refresh. Self-reported knowledge does not add skills to the resume or promise selection. Existing weekly plans are displayed as daily checklists without overwriting their data.

## Tests

```powershell
.\env\Scripts\python.exe manage.py test accounts.tests --settings=careerdna_core.test_settings
.\env\Scripts\python.exe manage.py test accounts.browser_checks --settings=careerdna_core.test_settings
```

Browser checks require Playwright and installed Microsoft Edge. Install the optional dependency only if needed:

```powershell
.\env\Scripts\python.exe -m pip install -r requirements-browser.txt
```

To verify the full live flow against the application already running on port 8001 with a synthetic PDF and real Gemini:

```powershell
.\env\Scripts\python.exe manage.py verify_running_resume --synthetic --full-flow
```

This creates a temporary account, checks persisted review/plan results and checkbox progress, then removes only that account, session and uploaded verification PDF. It checks that existing profiles and `.env` are unchanged. Do not edit application files during live verification: Django may reload while a request is pending.
