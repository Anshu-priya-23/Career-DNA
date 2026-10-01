"""Unmocked HTTP verification; only use a saved resume with its owner's permission."""
import hashlib
import secrets
import tempfile
import threading
import time
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.wsgi import get_wsgi_application
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test.utils import override_settings
from accounts.models import UserProfile

class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass

class Command(BaseCommand):
    help='Run authenticated HTTP analysis with a saved PDF and live Gemini using a disposable account. Requires owner permission to send the resume.'
    def add_arguments(self, parser):
        parser.add_argument('--profile-id',type=int,required=True)
    def handle(self,*args,**options):
        source=UserProfile.objects.get(pk=options['profile_id'])
        content=Path(source.resume.path).read_bytes()
        env_hash=hashlib.sha256((settings.BASE_DIR/'.env').read_bytes()).hexdigest()
        snapshot=list(UserProfile.objects.order_by('pk').values())
        username='verification_'+secrets.token_hex(8)
        password=secrets.token_urlsafe(32)
        test_user=User.objects.create_user(username=username,password=password)
        test_session_key = None
        try:
            with tempfile.TemporaryDirectory(prefix='careerdna-http-') as media, override_settings(MEDIA_ROOT=media):
                server=make_server('127.0.0.1',0,get_wsgi_application(),handler_class=QuietHandler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base=f'http://127.0.0.1:{server.server_port}'
                try:
                    with requests.Session() as session:
                        session.trust_env=False # loopback HTTP only, no change to Gemini's network environment
                        session.get(base+'/accounts/login/',timeout=10).raise_for_status()
                        csrf=session.cookies['csrftoken']
                        login=session.post(base+'/accounts/login/',data={'csrfmiddlewaretoken':csrf,'username':username,'password':password},timeout=10)
                        login.raise_for_status()
                        test_session_key = session.cookies.get(settings.SESSION_COOKIE_NAME)
                        csrf=session.cookies['csrftoken']
                        self.stdout.write(f'LIVE HTTP POST /accounts/profile/; saved PDF profile_id={source.pk}; bytes={len(content)}');self.stdout.flush()
                        started=time.monotonic()
                        response=session.post(base+'/accounts/profile/',data={'csrfmiddlewaretoken':csrf,'action':'analyze','dream_role':source.dream_role or 'Software developer','job_description':source.job_description},files={'resume':('verification.pdf',content,'application/pdf')},timeout=300)
                        test_user.profile.refresh_from_db()
                        result=test_user.profile.assessment
                        self.stdout.write(f'HTTP status={response.status_code}; redirects={[r.status_code for r in response.history]}; assessment_saved={bool(result)}; elapsed={time.monotonic()-started:.1f}s')
                        if not result:
                            from html import unescape
                            import re
                            alerts=re.findall(r'<p role="alert"[^>]*>(.*?)</p>',response.text,re.S)
                            for alert in alerts:self.stdout.write(unescape(re.sub('<[^>]+>','',alert)))
                            raise CommandError('Live HTTP analysis did not save an assessment.')
                        if 'Assessment saved.' not in response.text or 'Resume quality:' not in response.text:
                            raise CommandError('Result saved but success/result rendering failed.')
                        self.stdout.write(self.style.SUCCESS(f'LIVE HTTP SUCCESS: quality={result["quality_score"]}; corrections={len(result["corrections"])}; results rendered and persisted.'))
                        self.stdout.write('Model used: ' + result.get('model_used', 'unknown'))
                        if result.get('provider_notice') and result['provider_notice'] not in response.text:
                            raise CommandError('Fallback model notice was not rendered.')
                finally:
                    server.shutdown();server.server_close();thread.join(timeout=5)
        finally:
            if test_session_key:
                Session.objects.filter(session_key=test_session_key).delete()
            test_user.delete()
            if snapshot != list(UserProfile.objects.order_by('pk').values()):
                raise CommandError('Existing profile state changed during verification; inspect concurrent writes.')
            if env_hash != hashlib.sha256((settings.BASE_DIR/'.env').read_bytes()).hexdigest():
                raise CommandError('.env changed during verification.')
            if content != Path(source.resume.path).read_bytes():
                raise CommandError('Original resume file changed during verification.')
            self.stdout.write('Verified: original profiles and .env unchanged; disposable account and upload removed.')
