"""Submit a saved PDF through the browser form on the existing port 8001 server."""
import hashlib
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings
from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand, CommandError
from playwright.sync_api import sync_playwright
from accounts.models import UserProfile


class Command(BaseCommand):
    help = 'Verify the existing server with a real browser and saved resume; preserves existing profiles.'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', type=int)
        parser.add_argument('--synthetic', action='store_true')
        parser.add_argument('--backend-fixture', action='store_true')
        parser.add_argument('--current-roadmap', action='store_true', help='Use the saved assessment and current resume/JD instead of reanalysing them.')
        parser.add_argument('--known', action='append', default=[])
        parser.add_argument('--days', type=int, default=30)
        parser.add_argument('--hours-per-day', type=float, default=1)
        parser.add_argument('--full-flow', action='store_true')
        parser.add_argument('--screenshot', action='store_true', help='Save synthetic-data desktop/mobile previews in the project directory.')

    def handle(self, *args, **options):
        if options['synthetic']:
            import fitz
            source = None
            with fitz.open() as document:
                document.new_page().insert_text((72, 72), 'Alex Example\nalex@example.com\nEducation\nBSc Computer Science\nSkills\nJava\nProjects\nBuilt a small application.' if options['backend_fixture'] else 'Alex Example\nalex@example.com\nEducation\nBSc Computer Science\nSkills\nPython\nProjects\nBuilt a Python invoice validator with unit tests and CSV output.')
                content = document.tobytes()
        elif options['profile_id']:
            source = UserProfile.objects.get(pk=options['profile_id'])
            content = Path(source.resume.path).read_bytes()
        else:
            raise CommandError('Specify --synthetic or an authorized --profile-id.')
        env_hash = hashlib.sha256((settings.BASE_DIR / '.env').read_bytes()).digest()
        snapshot = list(UserProfile.objects.order_by('pk').values())
        user = User.objects.create_user(username='verification_' + secrets.token_hex(8))
        if options['current_roadmap']:
            if source is None:
                user.delete()
                raise CommandError('--current-roadmap requires --profile-id.')
            copied = user.profile
            copied.assessment = source.assessment
            copied.preparation = source.preparation
            copied.resume = source.resume.name
            copied.dream_role = source.dream_role
            copied.job_description = source.job_description
            copied.save()
        session = SessionStore()
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        base = 'http://127.0.0.1:8001'
        try:
            with sync_playwright() as engine:
                browser = engine.chromium.launch(channel='msedge', headless=True)
                context = browser.new_context()
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': session.session_key, 'url': base}])
                page = context.new_page()
                response = page.goto(base + '/accounts/profile/')
                self.stdout.write(f'Existing server GET status={response.status}')
                if options['current_roadmap']:
                    self.verify_current_plan(page, user, source, options)
                    browser.close()
                    return
                page.locator('[name=dream_role]').fill((source.dream_role or 'Software developer') if source else 'Junior Python developer')
                page.locator('[name=job_description]').fill(source.job_description if source else ('Junior backend developer. Java, Spring Boot, REST APIs, PostgreSQL and JWT required. Docker preferred.' if options['backend_fixture'] else 'Junior developer. Freshers accepted. Python, SQL and REST APIs required. Git preferred.'))
                page.locator('[name=resume]').set_input_files({'name': 'verification.pdf', 'mimeType': 'application/pdf', 'buffer': content})
                started = time.monotonic()
                self.stdout.write(f'Submitting {"saved resume from profile_id=" + str(source.pk) if source else "synthetic resume"} through browser form on port 8001.'); self.stdout.flush()
                with page.expect_response(lambda r: r.request.method == 'POST' and '/accounts/profile/' in r.url, timeout=300000) as submitted:
                    page.get_by_role('button', name='Review my resume', exact=True).click(timeout=300000)
                page.wait_for_load_state('networkidle', timeout=300000)
                # Playwright drives an event loop on this thread; run ORM reads separately.
                with ThreadPoolExecutor(max_workers=1) as executor:
                    result_profile = executor.submit(lambda: UserProfile.objects.get(user=user)).result()
                result = result_profile.assessment
                if len(result.get('edits', [])) > 5:
                    raise CommandError('Review exceeded five edits.')
                alerts = page.locator('[role=alert]').all_text_contents()
                self.stdout.write(f'POST status={submitted.value.status}; persisted={bool(result)}; elapsed={time.monotonic()-started:.1f}s; alerts={alerts}')
                if not result:
                    raise CommandError('Existing-server browser submission failed; inspect sanitized diagnostic log.')
                reloaded = page.reload()
                rendered = page.get_by_role('heading', name='What to improve', exact=True).is_visible()
                self.stdout.write(f'Reload GET status={reloaded.status}; persisted/rendered={rendered}; model={result.get("model_used")}; concise_edits={len(result.get("edits", []))}')
                if not rendered or 'Assessment saved.' not in alerts:
                    raise CommandError('Persistence or result rendering failed.')
                if options['full_flow']:
                    from accounts.services import learning_gaps
                    from playwright.sync_api import expect
                    expect(page.locator('#learning-inputs')).to_be_hidden()
                    page.get_by_role('link', name='Yes, create my plan', exact=True).click()
                    gaps = learning_gaps(result)
                    if len(gaps) < 2:
                        raise CommandError('Full-flow fixture needs at least two skill gaps.')
                    page.locator('.known input[type=checkbox]').first.check()
                    known = gaps[0]['skill']
                    page.locator('[name=days]').fill('28')
                    page.locator('[name=hours_per_day]').fill('2')
                    with page.expect_response(lambda r: r.request.method == 'POST' and '/accounts/profile/' in r.url, timeout=300000) as planned:
                        page.get_by_role('button', name='Generate my plan', exact=True).click(timeout=300000)
                    page.wait_for_load_state('networkidle', timeout=300000)
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        saved = executor.submit(lambda: UserProfile.objects.get(user=user)).result()
                    roadmap = saved.roadmap
                    self.stdout.write(f'Plan response: POST={planned.value.status}; persisted={bool(roadmap)}; phases={len(roadmap.get("phases", []))}; deferred={len(roadmap.get("deferred", []))}; alerts={page.locator("[role=alert]").all_text_contents()}')
                    if not roadmap or not roadmap.get('phases'):
                        raise CommandError('No live learning checklist persisted; inspect diagnostics.')
                    if any(p['requirement'] == known for p in roadmap['phases']):
                        raise CommandError('Known skill incorrectly included in plan.')
                    if roadmap['planned_hours'] > 56 or any(p['end_day'] > 28 for p in roadmap['phases']):
                        raise CommandError('Daily budget exceeded.')
                    checkbox = page.locator('.task').first
                    checkbox.check()
                    expect(page.locator('#request-state')).to_have_text('Progress saved.')
                    refreshed = page.reload()
                    expect(page.locator('.task').first).to_be_checked()
                    self.stdout.write(f'PLAN POST={planned.value.status}; reload GET={refreshed.status}; phases={len(roadmap["phases"])}; hours={roadmap["planned_hours"]}/56; deferred={len(roadmap["deferred"])}; known skill excluded=True; checkbox persisted after refresh=True')
                    if options['screenshot'] and options['synthetic']:
                        page.screenshot(path=str(settings.BASE_DIR / 'browser-preview.png'), full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    if page.evaluate('document.documentElement.scrollWidth') > 390:
                        raise CommandError('Mobile layout overflows.')
                    self.stdout.write('Responsive browser check passed at 390px.')
                    if options['screenshot'] and options['synthetic']:
                        page.screenshot(path=str(settings.BASE_DIR / 'browser-preview-mobile.png'), full_page=True)
                browser.close()
        finally:
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(self.cleanup, user, session, snapshot, env_hash, source, content).result()

    def cleanup(self, user, session, snapshot, env_hash, source, content):
            disposable = UserProfile.objects.get(user=user)
            if disposable.resume and (source is None or disposable.resume.name != source.resume.name):
                disposable.resume.delete(save=False)
            session.delete()
            user.delete()
            if snapshot != list(UserProfile.objects.order_by('pk').values()):
                raise CommandError('Existing profile state changed; inspect concurrent activity.')
            if env_hash != hashlib.sha256((settings.BASE_DIR / '.env').read_bytes()).digest():
                raise CommandError('.env changed during verification.')
            if source and content != Path(source.resume.path).read_bytes():
                raise CommandError('Original resume changed during verification.')
            self.stdout.write('Existing profiles, original resume and .env unchanged; verification account/session/upload removed.')

    def verify_current_plan(self, page, user, source, options):
        from accounts.services import learning_gaps
        from accounts.roadmap_graph import grounded_catalog, skill_key
        from accounts.ai_parser import extract_text_from_pdf
        from playwright.sync_api import expect
        catalog = dict(source.assessment, requirements=grounded_catalog(source.assessment,extract_text_from_pdf(source.resume)))
        gaps = learning_gaps(catalog)
        known = options['known'] or source.preparation.get('known_skills', [])
        page.get_by_role('link',name='Yes, create my plan',exact=True).click()
        for i,row in enumerate(gaps):
            page.get_by_label(row['skill'],exact=True).set_checked(skill_key(row['skill']) in {skill_key(s) for s in known})
        page.locator('[name=days]').fill(str(options['days']))
        page.locator('[name=hours_per_day]').fill(str(options['hours_per_day']))
        self.stdout.write(f'Current saved resume/JD profile={source.pk}; confirmed={known}; budget={options["days"]*options["hours_per_day"]}h'); self.stdout.flush()
        with page.expect_response(lambda r:r.request.method=='POST' and '/accounts/profile/' in r.url,timeout=300000) as submitted:
            page.get_by_role('button',name='Generate my plan',exact=True).click(timeout=300000)
        page.wait_for_load_state('networkidle',timeout=300000)
        with ThreadPoolExecutor(max_workers=1) as executor:
            saved=executor.submit(lambda:UserProfile.objects.get(user=user)).result()
        self.stdout.write(f'Current roadmap POST={submitted.value.status}; alerts={page.locator("[role=alert]").all_text_contents()}')
        if submitted.value.status!=302 or not saved.roadmap.get('phases'):
            raise CommandError('This exact saved-review roadmap submission still failed or scheduled no checklist.')
        if saved.roadmap['planned_hours']>options['days']*options['hours_per_day']:
            raise CommandError('Time budget exceeded.')
        catalog_ids = {r['id'] for r in saved.assessment['requirements']}
        if any(t.get('requirement_id') not in catalog_ids for t in saved.roadmap['phases']):
            raise CommandError('Saved tasks are not linked to persisted catalog IDs.')
        page.locator('.task').first.check()
        expect(page.locator('#request-state')).to_have_text('Progress saved.')
        response=page.reload()
        expect(page.locator('.task').first).to_be_checked()
        self.stdout.write(f'CURRENT CASE SUCCESS: GET={response.status}; phases={len(saved.roadmap["phases"])}; hours={saved.roadmap["planned_hours"]}; deferred={len(saved.roadmap["deferred"])}; IDs persisted=True; checkbox persisted after refresh=True')
