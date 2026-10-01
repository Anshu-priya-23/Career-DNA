"""Optional real-browser checks; Gemini is mocked, Django and JavaScript are real."""
import tempfile
from unittest.mock import patch
from django.test import LiveServerTestCase, override_settings
from django.contrib.auth.models import User
from playwright.sync_api import sync_playwright, expect
from .tests import pdf, review, topic, JD, id_plan
from .schemas import TopicPlan
from .ai_parser import AnalysisError

class BrowserFlowTests(LiveServerTestCase):
    def test_full_journey_and_failed_retry(self):
        with tempfile.TemporaryDirectory(prefix='careerdna-browser-') as media, override_settings(MEDIA_ROOT=media):
            user=User.objects.create_user('browser-student',password='test-password')
            self.client.force_login(user)
            with sync_playwright() as engine:
                browser=engine.chromium.launch(channel='msedge',headless=True)
                context=browser.new_context(viewport={'width':1280,'height':900})
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(self.live_server_url+'/accounts/profile/')
                page.locator('[name=dream_role]').fill('Junior Python developer')
                page.locator('[name=job_description]').fill('Python required.')
                page.locator('[name=resume]').set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':pdf().read()})
                with patch('accounts.views.review_resume',return_value=review('Python')):
                    page.get_by_role('button',name='Review my resume',exact=True).click()
                    expect(page.get_by_text('Assessment saved.',exact=True)).to_be_visible()
                expect(page.get_by_role('heading',name='What to improve')).to_be_visible()
                expect(page.locator('#learning-inputs')).to_be_hidden()
                page.get_by_role('link',name='Yes, create my plan').click()
                page.locator('[name=days]').fill('14')
                page.locator('[name=hours_per_day]').fill('1')
                with patch('accounts.views.plan_topics',return_value=id_plan(topic())):
                    page.get_by_role('button',name='Generate my plan',exact=True).click()
                    expect(page.get_by_text('Learning plan saved.',exact=True)).to_be_visible()
                page.get_by_role('checkbox',name='Complete Python foundations').check()
                expect(page.locator('#request-state')).to_have_text('Progress saved.')
                page.reload()
                expect(page.get_by_role('checkbox',name='Complete Python foundations')).to_be_checked()
                # Failure keeps previous rendered plan and saved progress; retry control remains usable.
                page.get_by_role('link',name='Yes, create my plan').click()
                with patch('accounts.views.plan_topics',side_effect=AnalysisError('Synthetic service outage. Retry later.')):
                    page.get_by_role('button',name='Generate my plan',exact=True).click()
                    expect(page.get_by_text('Synthetic service outage. Retry later.',exact=True)).to_be_visible()
                expect(page.get_by_role('checkbox',name='Complete Python foundations')).to_be_checked()
                expect(page.get_by_role('button',name='Generate my plan',exact=True)).to_be_enabled()
                # Native form validation must not activate the working state.
                page.locator('[name=hours_per_day]').fill('0')
                page.get_by_role('button',name='Generate my plan',exact=True).click()
                expect(page.locator('#request-state')).to_have_text('')
                page.set_viewport_size({'width':390,'height':844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                self.assertEqual(errors,[])
                browser.close()
