from django.core.management.base import BaseCommand, CommandError
from accounts.ai_parser import review_resume, plan_topics, AnalysisError
from accounts.services import assess, schedule

class Command(BaseCommand):
    help = 'LIVE Gemini integration smoke test with synthetic documents only; writes no user data.'
    def handle(self, *args, **options):
        text = 'Alex Example\nalex@example.com\nEducation\nBSc Computer Science\nSkills\nPython\nProjects\nBuilt a Python invoice validator with unit tests and CSV output.'
        jd = 'Junior developer. Freshers accepted. Python required. SQL preferred.'
        try:
            review = review_resume(text, 'Junior developer', jd)
            result = assess(text, 'Junior developer', jd, review)
            self.stdout.write('LIVE assessment schema and evidence validation passed.')
            preferences = dict(weeks=4, hours_per_week=7, programming_language='Python', resource_language='English', current_skills='Python beginner')
            plan = plan_topics({'role': 'Junior developer', 'jd': jd, 'gaps': result['requirements'], 'preferences': preferences})
            roadmap = schedule(plan, preferences, result['requirements'])
            self.stdout.write(self.style.SUCCESS(f"LIVE roadmap validated: {len(roadmap['phases'])} phases, {roadmap['planned_hours']}/{roadmap['budget']} hours. This is a smoke test, not an accuracy benchmark."))
        except AnalysisError as exc:
            raise CommandError(str(exc)) from None
