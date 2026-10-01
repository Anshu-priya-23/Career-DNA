from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class UserProfile(models.Model):
    job_description = models.TextField(blank=True, default='')
    assessment = models.JSONField(default=dict, blank=True)
    roadmap = models.JSONField(default=dict, blank=True)
    preparation = models.JSONField(default=dict, blank=True)
    # Choice lists for Dropdowns
    ROLE_CHOICES = [
        ('Full Stack Dev', 'Full Stack Developer'),
        ('Backend Dev', 'Backend Developer'),
        ('Frontend Dev', 'Frontend Developer'),
        ('Cybersecurity Analyst', 'Cybersecurity Analyst'),
        ('Cloud Engineer', 'Cloud Engineer'),
        ('Data Scientist', 'Data Scientist'),
    ]

    COMPANY_CHOICES = [
        ('SAP', 'SAP'),
        ('Google', 'Google'),
        ('Microsoft', 'Microsoft'),
        ('Amazon', 'Amazon'),
        ('Oracle', 'Oracle'),
        ('TCS', 'TCS'),
        ('Infosys', 'Infosys'),
    ]

    # Links this profile to a specific registered User.
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    resume = models.FileField(upload_to='resumes/', blank=True, null=True)

    # AI Extracted Data
    skills = models.TextField(blank=True, null=True, help_text="AI-parsed skills will be stored here.")
    missing_skills = models.TextField(blank=True, null=True)
    study_plan = models.TextField(blank=True, null=True)

    # Academic Details
    college = models.CharField(max_length=255, blank=True, null=True)
    degree = models.CharField(max_length=100, blank=True, null=True)
    graduation_year = models.IntegerField(blank=True, null=True)

    # Career Targets (Updated to use Dropdowns)
    dream_company = models.CharField(max_length=100, choices=COMPANY_CHOICES, blank=True, null=True)
    dream_role = models.CharField(max_length=100, blank=True, null=True)

    # Social footprint
    github_url = models.URLField(max_length=200, blank=True, null=True)
    linkedin_url = models.URLField(max_length=200, blank=True, null=True)

    # Resume Score
    resume_score = models.CharField(
        max_length=2,
        blank=True,
        null=True,
        help_text="Letter grade (A+, B-, etc.)"
    )

    def __str__(self):
        return f"{self.user.username}'s Profile"


# NEW MODEL: Stores each roadmap task individually
class RoadmapTask(models.Model):
    profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='roadmap_tasks'
    )
    task_description = models.CharField(max_length=255)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.profile.user.username} - {self.task_description}"


# --- AUTOMATION SIGNALS ---
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
