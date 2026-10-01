import io
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
import fitz
from django.test import TestCase, SimpleTestCase, override_settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from .ai_parser import AnalysisError, extract_text_from_pdf, generate
from .schemas import Review, TopicPlan
from .services import assess, schedule, supported
from .forms import RoadmapForm
from .models import RoadmapTask
from .resources import resources_for
from .roadmap_graph import requirement_id

TEXT = 'Alex\nalex@example.com\nEducation\nBSc Computer Science\nSkills\nPython SQL\nProjects\nBuilt a Python service that validates invoices and saves users time.'
JD = 'Python required. SQL preferred.'

def review(quote='Built a Python service that validates invoices and saves users time.', requirement='Python required.', skill='Python', **kwargs):
    item = dict(skill=skill, kind='skill', optional=False, requirement_quote=requirement, resume_quote=quote, demonstrated=True, explanation='Check project evidence.', demonstration='Build and test a working application.')
    item.update(kwargs)
    return Review(corrections=[], requirements=[item])

def topic(key='a', prereqs=None, requirement='Python'):
    return dict(id=key,title='Python foundations',requirement=requirement,prerequisites=prereqs or [],objectives='Write functions',learning_hours=2,practice_hours=3,revision_hours=1,assessment_hours=1,exercise='Implement three functions and tests',project='Build invoice validator',checkpoint='Pass five tests without hints')

def pdf(text=TEXT):
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((50,50),text)
        return SimpleUploadedFile('resume.pdf',doc.tobytes(),content_type='application/pdf')

def id_plan(*rows):
    from .roadmap_graph import requirement_id
    return TopicPlan(topics=[dict(r,requirement_id=requirement_id(r['requirement'])) for r in rows])

class EvidenceTests(SimpleTestCase):
    def test_whitespace_punctuation_and_ligatures(self):
        self.assertTrue(supported('Built a Python service', 'Built a\nPython — service'))
        self.assertTrue(supported('efficient file', 'e\ufb03cient \ufb01le'))
        self.assertTrue(supported('development', 'develop-\nment'))
        self.assertFalse(supported('Java', 'JavaScript'))
        self.assertFalse(supported('C++', 'C'))
        self.assertFalse(supported('', TEXT))
    def test_strong_vs_weak_and_no_arbitrary_grade(self):
        strong=assess(TEXT,'Developer',JD,review())
        weak=assess('Skills Python','Developer',JD,review('Python'))
        self.assertGreater(strong['quality_score'],weak['quality_score'])
        self.assertEqual(strong['job_fit'],100)
        self.assertEqual(weak['job_fit'],25)
        self.assertEqual(sum(r['earned'] for r in strong['rubric']),strong['quality_score'])
    def test_unsupported_quote_downgraded_without_crash(self):
        result=assess(TEXT,'Developer',JD,review('Built a Python rocket for NASA and saved millions'))
        self.assertEqual(result['job_fit'],25)
        self.assertTrue(result['warnings'])
        self.assertEqual(result['requirements'][0]['resume_quote'],'')
    def test_absent(self):
        result=assess(TEXT,'Developer','Rust required.',review('', 'Rust required.', 'Rust'))
        self.assertEqual(result['job_fit'],0)
        self.assertEqual(result['requirements'][0]['group'],'absent')
    def test_senior_vs_fresher(self):
        senior=review('', '5 years professional experience required.', '5 years professional experience',kind='experience')
        self.assertEqual(assess(TEXT,'Senior','5 years professional experience required.',senior)['job_fit'],0)
        fresher=assess(TEXT,'Junior','Freshers accepted. Python required.',review())
        self.assertEqual(fresher['job_fit'],100)
        self.assertFalse(any('professional experience' == r['skill'] for r in fresher['requirements']))
    def test_missing_jd_is_general(self):
        result=assess(TEXT,'Developer','',review())
        self.assertIsNone(result['job_fit'])
        self.assertTrue(result['general'])
        self.assertEqual(result['requirements'],[])
    def test_invented_requirement_rejected(self):
        with self.assertRaises(AnalysisError):
            assess(TEXT,'Dev',JD,review('', 'Rust mandatory.', 'Rust'))
        with self.assertRaises(AnalysisError):
            assess(TEXT,'Dev',JD,review('', 'Python required.', 'Rust'))
    def test_optional_not_scored(self):
        result=assess(TEXT,'Dev',JD,review('', 'SQL preferred.', 'SQL',optional=True))
        self.assertIsNone(result['job_fit'])
        self.assertEqual(result['requirements'][0]['group'],'optional')
    def test_invented_wording_replaced(self):
        model=Review(corrections=[dict(section='Projects',priority='high',issue='Improve clarity',why='Show work',correction='Explain contribution',evidence='',suggested_wording='Built [app] and improved revenue by 90%')],requirements=[])
        result=assess(TEXT,'Dev','',model)
        self.assertNotIn('90%',result['corrections'][0]['suggested_wording'])
    def test_bad_and_empty_pdf(self):
        for value in [io.BytesIO(b'bad'),pdf(''),pdf('tiny')]:
            with self.assertRaises(AnalysisError): extract_text_from_pdf(value)
    def test_valid_pdf(self):
        self.assertIn('Python',extract_text_from_pdf(pdf()))

class RoadmapTests(SimpleTestCase):
    preferences=dict(weeks=2,hours_per_week=7,resource_language='English',programming_language='Python')
    requirements=[{'skill':'Python','optional':False}]
    def test_topological_order_and_hours(self):
        plan=TopicPlan(topics=[topic('b',['a']),topic('a')])
        result=schedule(plan,self.preferences,self.requirements)
        self.assertEqual([p['id'] for p in result['phases']],['a','b'])
        self.assertEqual(result['planned_hours'],14)
        self.assertEqual(result['phases'][-1]['end_week'],2)
        self.assertLessEqual(result['planned_hours'],result['budget'])
    def test_tiny_budget_defers_not_compresses(self):
        result=schedule(TopicPlan(topics=[topic()]),dict(self.preferences,weeks=1,hours_per_week=.5),self.requirements)
        self.assertEqual(result['phases'],[])
        self.assertEqual(len(result['deferred']),1)
    def test_deferred_prerequisite_blocks_child(self):
        first=topic('a');first['learning_hours']=20
        result=schedule(TopicPlan(topics=[first,topic('b',['a'])]),self.preferences,self.requirements)
        self.assertEqual(len(result['deferred']),2)
    def test_invalid_graphs(self):
        for topics in [[topic('a',['b']),topic('b',['a'])],[topic('a',['missing'])],[topic(),topic()],[topic(requirement='Unrelated')]]:
            with self.assertRaises(AnalysisError):schedule(TopicPlan(topics=topics),self.preferences,self.requirements)
    def test_resource_provenance_and_fallback(self):
        resources=resources_for(topic(),self.preferences)
        self.assertTrue(all(r['verified'] and r['source'].startswith('https:') for r in resources))
        other=resources_for(dict(topic(),title='Rust',requirement='Rust'),self.preferences)
        self.assertTrue(all(not r['verified'] and ('search' in r['url'] or 'results?' in r['url']) for r in other))
    def test_invalid_time(self):
        for hours in ['NaN','inf','0','100']:
            form=RoadmapForm(dict(days=1,hours_per_day=hours))
            self.assertFalse(form.is_valid())
    def test_daily_budget_and_day_ranges(self):
        form=RoadmapForm(dict(days=10,hours_per_day=1.5))
        self.assertTrue(form.is_valid(),form.errors)
        result=schedule(TopicPlan(topics=[topic('b',['a']),topic('a')]),form.cleaned_data,self.requirements)
        self.assertEqual(result['budget'],15)
        self.assertEqual([(p['start_day'],p['end_day']) for p in result['phases']],[(1,5),(5,10)])
        self.assertLessEqual(result['phases'][-1]['end_day'],10)

@override_settings(MEDIA_ROOT=tempfile.gettempdir()+'/careerdna-test-media')
class FlowTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('student',password='test-pass')
        self.client.force_login(self.user)
        self.url=reverse('profile')
    def post_analysis(self):
        return self.client.post(self.url,{'action':'analyze','dream_role':'Junior Python developer','job_description':'Python required.','resume':pdf()})
    @patch('accounts.views.review_resume')
    def test_upload_analysis_render_and_roadmap(self,mock):
        mock.return_value=review('Python')
        self.assertEqual(self.post_analysis().status_code,302)
        response=self.client.get(self.url)
        self.assertContains(response,'What to improve')
        self.assertNotContains(response,'Resume quality:')
        self.assertNotContains(response,'name="college"')
        content=response.content.decode()
        headings=['Resume review','What to improve','Skills to strengthen','Do you want to build']
        self.assertEqual(sorted(content.index(h) for h in headings),[content.index(h) for h in headings])
        with patch('accounts.views.plan_topics',return_value=id_plan(topic())):
            response=self.client.post(self.url,dict(action='roadmap',days=14,hours_per_day=1))
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(self.url),'Python foundations')
        self.assertEqual(RoadmapTask.objects.count(),1)
    @patch('accounts.views.review_resume')
    def test_failure_preserves_results_and_resume(self,mock):
        mock.return_value=review(); self.post_analysis()
        profile=self.user.profile;profile.refresh_from_db()
        old=profile.assessment; old_file=profile.resume.name
        mock.side_effect=AnalysisError('Provider unavailable')
        response=self.post_analysis()
        self.assertContains(response,'Provider unavailable')
        profile.refresh_from_db()
        self.assertEqual(profile.assessment,old)
        self.assertEqual(profile.resume.name,old_file)
    def test_roadmap_failure_preserves_tasks(self):
        profile=self.user.profile
        profile.assessment=assess(TEXT,'Dev',JD,review('Python'));profile.roadmap={'old':True};profile.save()
        task=RoadmapTask.objects.create(profile=profile,task_description='Old task')
        with patch('accounts.views.plan_topics',side_effect=AnalysisError('Timeout')):
            response=self.client.post(self.url,dict(action='roadmap',days=14,hours_per_day=1))
        self.assertContains(response,'Timeout')
        profile.refresh_from_db();self.assertEqual(profile.roadmap,{'old':True})
        self.assertTrue(RoadmapTask.objects.filter(pk=task.pk).exists())
    def test_task_auth_method_and_validation(self):
        task=RoadmapTask.objects.create(profile=self.user.profile,task_description='Test')
        url=reverse('update_task',args=[task.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        for value in ['bad','[]','{"completed":"false"}']:
            self.assertEqual(self.client.post(url,value,content_type='application/json').status_code,400)
        self.assertEqual(self.client.post(url,json.dumps({'completed':True}),content_type='application/json').status_code,200)
        task.refresh_from_db();self.assertTrue(task.is_completed)
        other=User.objects.create_user('other');self.client.force_login(other)
        self.assertEqual(self.client.post(url,'{"completed":false}',content_type='application/json').status_code,404)
        self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)
    def test_csrf_enforced(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(self.url,{'action':'save'}).status_code,403)
    def test_invalid_upload_and_missing_role(self):
        self.assertContains(self.client.post(self.url,{'action':'analyze','job_description':'Python required.','resume':pdf()}),'Enter a target role')
        self.assertContains(self.client.post(self.url,{'action':'analyze','dream_role':'Dev','job_description':JD,'resume':SimpleUploadedFile('x.pdf',b'bad')}),'valid PDF')

    def test_known_skills_skip_provider_and_preserve_profile_fields(self):
        profile=self.user.profile
        profile.assessment=assess(TEXT,'Developer','Python required.',review('Python'))
        profile.college='Existing college';profile.degree='Existing degree';profile.save()
        with patch('accounts.views.plan_topics') as planner:
            response=self.client.post(self.url,dict(action='roadmap',days=10,hours_per_day=1,**{'skill_'+requirement_id('Python'):'on'}))
        self.assertEqual(response.status_code,302)
        planner.assert_not_called()
        profile.refresh_from_db()
        self.assertEqual(profile.roadmap['phases'],[])
        with patch('accounts.views.review_resume',return_value=review()):self.post_analysis()
        profile.refresh_from_db()
        self.assertEqual(profile.college,'Existing college')
        self.assertEqual(profile.degree,'Existing degree')

    def test_missing_target_ids_are_completed_without_discarding_valid_tasks(self):
        from .roadmap_graph import grounded_catalog
        profile=self.user.profile
        jd='Git and React required.'
        profile.assessment={'role':'Developer','jd':jd,'requirements':grounded_catalog({'jd':jd,'requirements':[]},TEXT)}
        profile.save()
        with patch('accounts.views.plan_topics',side_effect=[id_plan(topic('git',requirement='Git')),id_plan(topic('react',requirement='React'))]) as planner:
            response=self.client.post(self.url,dict(action='roadmap',days=14,hours_per_day=1))
        self.assertEqual(response.status_code,302)
        profile.refresh_from_db()
        self.assertEqual([t['requirement'] for t in profile.roadmap['phases']],['Git','React'])
        self.assertEqual([r['skill'] for r in planner.call_args.args[0]['gaps']],['React'])

    def test_known_skills_excluded_from_generated_plan(self):
        profile=self.user.profile
        data=Review(corrections=[],requirements=[review('Python').requirements[0],review('', 'SQL preferred.', 'SQL',optional=True).requirements[0]])
        profile.assessment=assess(TEXT,'Dev',JD,data);profile.save()
        with patch('accounts.views.plan_topics',return_value=id_plan(topic(requirement='SQL'))) as planner:
            response=self.client.post(self.url,dict(action='roadmap',days=7,hours_per_day=1,**{'skill_'+requirement_id('Python'):'on'}))
        self.assertEqual(response.status_code,302)
        self.assertEqual([r['skill'] for r in planner.call_args.args[0]['gaps']],['SQL'])
        self.assertEqual(planner.call_args.args[0]['known_skills'],['Python'])

    def test_legacy_skill_preferences_do_not_mark_unknown_skills_known(self):
        profile=self.user.profile
        profile.assessment=assess(TEXT,'Developer',JD,review('Python'))
        profile.preparation={'weeks':2,'hours_per_week':7,'skill_0':'unknown'}
        profile.save()
        response=self.client.get(self.url)
        form=response.context['roadmap_form']
        self.assertFalse(form['skill_'+requirement_id('Python')].value())
        self.assertEqual(form['days'].value(),14)
        self.assertEqual(form['hours_per_day'].value(),1)

    def test_resume_download_requires_owner_login(self):
        profile=self.user.profile;profile.resume=pdf();profile.save()
        self.assertEqual(self.client.get(reverse('resume')).status_code,200)
        other=User.objects.create_user('download-other');self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('resume')).status_code,404)

class GeminiContractTests(SimpleTestCase):
    @patch.dict(os.environ,{'GEMINI_API_KEY':'test-only-placeholder'})
    @patch('google.genai.Client')
    def test_invalid_json_retried(self,client):
        call=client.return_value.__enter__.return_value.models.generate_content
        call.return_value.text='{broken'
        with self.assertRaisesMessage(AnalysisError,'invalid data twice'):
            generate(Review,'test',{})
        self.assertEqual(call.call_count,2)
    @patch.dict(os.environ,{'GEMINI_API_KEY':'test-only-placeholder'})
    @patch('google.genai.Client')
    def test_service_error_sanitized(self,client):
        client.return_value.__enter__.return_value.models.generate_content.side_effect=RuntimeError('SECRET PROVIDER BODY')
        with self.assertRaises(AnalysisError) as raised:generate(Review,'test',{})
        self.assertNotIn('SECRET',str(raised.exception))

class SchemaRegressionTests(SimpleTestCase):
    def test_wire_schema_preserves_property_names_and_local_bounds(self):
        from accounts.ai_parser import provider_schema
        schema=provider_schema(TopicPlan)
        topic_schema=schema['$defs']['Topic']
        self.assertIn('title',topic_schema['properties'])
        self.assertTrue(set(topic_schema['required']).issubset(topic_schema['properties']))
        self.assertNotIn('maximum',topic_schema['properties']['learning_hours'])
        from pydantic import ValidationError
        invalid=topic();invalid['learning_hours']=0
        with self.assertRaises(ValidationError):TopicPlan(topics=[invalid])
    def test_wrong_degree_not_full_credit(self):
        data=review('BSc Computer Science','Masters degree required.','Masters degree',kind='education')
        self.assertLess(assess(TEXT,'Senior','Masters degree required.',data)['job_fit'],100)
    def test_too_few_years_not_full_credit(self):
        data=review('2 years professional experience','5 years professional experience required.','professional experience',kind='experience')
        self.assertLess(assess(TEXT+'\n2 years professional experience','Senior','5 years professional experience required.',data)['job_fit'],100)

    def test_unsupported_resource_language_uses_search(self):
        resources=resources_for(topic(),dict(resource_language='Hindi',programming_language='Python'))
        self.assertTrue(all(not r['verified'] for r in resources))

class DailyQuotaFallbackTests(SimpleTestCase):
    @patch.dict(os.environ,{'GEMINI_API_KEY':'test-only','GEMINI_MODEL':'gemini-2.5-flash','GEMINI_FALLBACK_MODEL':'gemini-3.1-flash-lite'})
    @patch('google.genai.Client')
    def test_provider_timeout_fails_over_once(self,client):
        from google.genai.errors import ServerError
        call=client.return_value.__enter__.return_value.models.generate_content
        call.side_effect=[ServerError(504,{'error':{'message':'private provider body'}}),MagicMock(text='{"corrections":[],"requirements":[]}')]
        result=generate(Review,'test',{})
        self.assertTrue(result._fallback_used)
        self.assertEqual([c.kwargs['model'] for c in call.call_args_list],['gemini-2.5-flash','gemini-3.1-flash-lite'])
        self.assertNotIn('quota',assess(TEXT,'Dev','',result)['provider_notice'])
        call.reset_mock()
        call.side_effect=ServerError(504,{'error':{'message':'private provider body'}})
        with self.assertRaisesMessage(AnalysisError,'HTTP 504'):generate(Review,'test',{})
        self.assertEqual(call.call_count,2)

    def quota_error(self, quota_id='GenerateRequestsPerDayPerProjectPerModel-FreeTier'):
        from google.genai.errors import ClientError
        return ClientError(429, {'error': {'code':429,'status':'RESOURCE_EXHAUSTED','message':'do not log this secret provider body', 'details':[
            {'@type':'type.googleapis.com/google.rpc.QuotaFailure','violations':[{'quotaId':quota_id,'quotaValue':'20','quotaMetric':'generativelanguage.googleapis.com/generate_content_free_tier_requests','subject':'private-project-id'}]},
            {'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'4s'}]}})

    @patch.dict(os.environ,{'GEMINI_API_KEY':'test-only','GEMINI_MODEL':'gemini-2.5-flash','GEMINI_FALLBACK_MODEL':'gemini-3.1-flash-lite'})
    @patch('google.genai.Client')
    def test_daily_model_quota_fails_over_and_records_provenance(self,client):
        call=client.return_value.__enter__.return_value.models.generate_content
        call.side_effect=[self.quota_error(),MagicMock(text='{"corrections":[],"requirements":[]}')]
        result=generate(Review,'test',{})
        self.assertEqual([c.kwargs['model'] for c in call.call_args_list],['gemini-2.5-flash','gemini-3.1-flash-lite'])
        self.assertTrue(result._fallback_used)
        self.assertEqual(result._model_used,'gemini-3.1-flash-lite')
        assessment=assess(TEXT,'Dev','',result)
        self.assertIn('fallback',assessment['provider_notice'])
        self.assertNotIn('_model_used',result.model_dump())

    @patch.dict(os.environ,{'GEMINI_API_KEY':'test-only','GEMINI_MODEL':'gemini-2.5-flash','GEMINI_FALLBACK_MODEL':'gemini-3.1-flash-lite'})
    @patch('google.genai.Client')
    def test_fallback_also_exhausted_stops(self,client):
        call=client.return_value.__enter__.return_value.models.generate_content
        call.side_effect=self.quota_error()
        with self.assertRaisesMessage(AnalysisError,'daily quota'):generate(Review,'test',{})
        self.assertEqual(call.call_count,2)

    @patch.dict(os.environ,{'GEMINI_API_KEY':'test-only','GEMINI_FALLBACK_MODEL':''})
    @patch('google.genai.Client')
    def test_fallback_can_be_disabled(self,client):
        call=client.return_value.__enter__.return_value.models.generate_content
        call.side_effect=self.quota_error()
        with self.assertRaises(AnalysisError):generate(Review,'test',{})
        self.assertEqual(call.call_count,1)

    @patch.dict(os.environ,{'GEMINI_API_KEY':'test-only'})
    @patch('google.genai.Client')
    def test_no_fallback_for_minute_global_or_network_errors(self,client):
        from google.genai.errors import ClientError
        import httpx
        call=client.return_value.__enter__.return_value.models.generate_content
        for error in [self.quota_error('RequestsPerMinutePerProject'),self.quota_error('RequestsPerDayPerProject'),ClientError(403,{'error':{'message':'denied'}}),httpx.ConnectError('private-proxy-secret'),httpx.ReadTimeout('private-request-secret')]:
            call.reset_mock();call.side_effect=error
            with self.assertRaises(AnalysisError):generate(Review,'test',{})
            self.assertEqual(call.call_count,1)

    def test_quota_diagnostics_do_not_expose_provider_body(self):
        from accounts.ai_parser import quota_diagnostics
        details=quota_diagnostics(self.quota_error())
        self.assertEqual(details['retry_seconds'],4)
        self.assertEqual(details['limits'][0]['quotaValue'],'20')
        self.assertNotIn('secret',str(details))
        self.assertNotIn('private-project',str(details))

class RoadmapGraphRepairTests(SimpleTestCase):
    def test_equivalent_requirement_and_title_reference(self):
        first=topic('base',requirement='RESTful APIs');first['title']='HTTP basics'
        child=topic('api',['HTTP basics'],requirement='REST API development')
        result=schedule(TopicPlan(topics=[child,first]),dict(days=14,hours_per_day=1),[{'skill':'REST APIs'}])
        self.assertEqual([p['id'] for p in result['phases']],['base','api'])
        self.assertTrue(all(p['requirement']=='REST APIs' for p in result['phases']))

    def test_known_skill_is_satisfied_not_missing_topic(self):
        result=schedule(TopicPlan(topics=[topic('spring',['Java foundations'],requirement='Spring Boot')]),dict(days=7,hours_per_day=1,known_skills=['Java']),[{'skill':'Spring Boot'}])
        self.assertEqual(result['phases'][0]['prerequisites'],[])

    def test_missing_related_foundation_is_inserted_and_budgeted(self):
        result=schedule(TopicPlan(topics=[topic('react',['JavaScript basics'],requirement='React')]),dict(days=7,hours_per_day=1),[{'skill':'React'}])
        self.assertEqual([p['id'] for p in result['phases']],['foundation_javascript'])
        self.assertEqual(result['deferred'][0]['title'],'Python foundations')
        self.assertEqual(result['planned_hours'],7)

    def test_unrelated_foundation_still_rejected(self):
        with self.assertRaises(AnalysisError):
            schedule(TopicPlan(topics=[topic('react',['Quantum mechanics'],requirement='React')]),dict(days=30,hours_per_day=2),[{'skill':'React'}])

    def test_foundation_requirement_assigned_to_related_gap(self):
        base=topic('js',requirement='JavaScript fundamentals')
        child=topic('react',['js'],requirement='React')
        result=schedule(TopicPlan(topics=[child,base]),dict(days=14,hours_per_day=1),[{'skill':'React'}])
        self.assertEqual([p['id'] for p in result['phases']],['js','react'])

class ConciseFeedbackTests(SimpleTestCase):
    def test_api_edit_points_to_actual_spring_project(self):
        from accounts.feedback import concise_edits
        edits,_=concise_edits('Projects\nBuilt a Spring Boot application.', 'REST APIs required.', [dict(skill='REST APIs',kind='skill',status='absent',optional=False,resume_quote='')], [])
        self.assertEqual(edits[0]['section'],'Projects')
        self.assertEqual(edits[0]['before'],'Built a Spring Boot application.')
        self.assertIn('using Spring Boot',edits[0]['after'])
        self.assertIn('actually implemented',edits[0]['after'])

    def test_five_edits_maximum_and_no_invented_metrics(self):
        from accounts.feedback import concise_edits
        requirements=[dict(skill='Skill'+str(i),kind='skill',status='listed',optional=False,resume_quote='Skills Python') for i in range(10)]
        edits,notice=concise_edits('Skills Python','Python required.',requirements,[])
        self.assertEqual(len(edits),5)
        self.assertTrue(all('before' in e and 'after' in e for e in edits))
        self.assertTrue(all('90%' not in e['after'] for e in edits))
        self.assertIn('does not explicitly',notice)

    def test_cgpa_requires_explicit_jd_eligibility(self):
        from accounts.feedback import concise_edits
        edits,notice=concise_edits('Education\nBSc Computer Science','Minimum CGPA 7.0 required.',[],[])
        self.assertEqual(edits[0]['section'],'Education')
        self.assertNotIn('7.0',edits[0]['after'])
        self.assertIn('actual CGPA',edits[0]['after'])
        edits,_=concise_edits('Education\nBSc Computer Science','Degree required.',[],[])
        self.assertEqual(edits,[])

class StableRequirementTests(SimpleTestCase):
    def test_git_required_and_version_control_have_same_id(self):
        from .roadmap_graph import requirement_id, grounded_catalog
        self.assertEqual(requirement_id('Git'),requirement_id('version control'))
        for jd in ('Git required.','Version control required.'):
            rows=grounded_catalog({'jd':jd,'requirements':[]},'Skills Python')
            result=schedule(id_plan(topic(requirement=rows[0]['skill'])),dict(days=7,hours_per_day=1),rows)
            self.assertEqual(result['phases'][0]['requirement_id'],requirement_id('Git'))

    def test_git_necessary_foundation_explicitly_linked(self):
        from .roadmap_graph import requirement_id
        base=dict(topic('git'),requirement_id=requirement_id('React'),topic_kind='foundation',foundation_skill='Git')
        child=dict(topic('react',['git'],requirement='React'),requirement_id=requirement_id('React'))
        result=schedule(TopicPlan(topics=[child,base]),dict(days=14,hours_per_day=1),[{'skill':'React'}])
        self.assertEqual([p['id'] for p in result['phases']],['git','react'])

    def test_unrelated_task_excluded_without_rejecting_valid_graph(self):
        unrelated=dict(topic('extra',requirement='Quantum mechanics'),topic_kind='unrelated',requirement_id='')
        good=dict(topic('react',requirement='React'),requirement_id=requirement_id('React'))
        result=schedule(TopicPlan(topics=[unrelated,good]),dict(days=7,hours_per_day=1),[{'skill':'React'}])
        self.assertEqual([p['id'] for p in result['phases']],['react'])

    def test_known_id_is_satisfied_prerequisite(self):
        from .roadmap_graph import requirement_id
        child=dict(topic('spring',[requirement_id('Java')],requirement='Spring Boot'),requirement_id=requirement_id('Spring Boot'))
        result=schedule(TopicPlan(topics=[child]),dict(days=7,hours_per_day=1,known_skills=[requirement_id('Java')]),[{'skill':'Java'},{'skill':'Spring Boot'}])
        self.assertEqual(result['phases'][0]['prerequisites'],[])

    def test_unknown_reference_rejected(self):
        bad=dict(topic(),requirement_id='req_invented')
        with self.assertRaises(AnalysisError):schedule(TopicPlan(topics=[bad]),dict(days=7,hours_per_day=1),[{'skill':'Python'}])
