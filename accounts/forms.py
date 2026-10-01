from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import UserProfile

# 1. Our clean Sign-Up form that strips out the default Django validation text
class StandardSignUpForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['username']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.help_text = None

# 2. Our Profile Dashboard form linked to your PostgreSQL database
class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['resume', 'dream_role', 'job_description']
        labels = {'resume': 'Resume PDF', 'dream_role': 'Target job role', 'job_description': 'Job description'}
        widgets = {
            'resume': forms.FileInput(attrs={'accept': '.pdf,application/pdf'}),
            'dream_role': forms.TextInput(attrs={'placeholder': 'e.g. Junior backend developer'}),
            'job_description': forms.Textarea(attrs={'rows': 6, 'placeholder': 'Paste the job requirements here'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dream_role'].required = True
        self.fields['dream_role'].error_messages['required'] = 'Enter a target role.'
        self.fields['job_description'].required = True

    def clean_job_description(self):
        value = self.cleaned_data['job_description']
        if len(value) > 20000:
            raise forms.ValidationError('Keep the JD below 20,000 characters.')
        return value

    def clean_resume(self):
        value = self.cleaned_data.get('resume')
        if value and hasattr(value, 'content_type'):
            from .ai_parser import extract_text_from_pdf, AnalysisError
            try:
                extract_text_from_pdf(value)
            except AnalysisError as exc:
                raise forms.ValidationError(str(exc))
        return value


class RoadmapForm(forms.Form):
    days = forms.IntegerField(label='Days available', min_value=1, max_value=730, initial=30)
    hours_per_day = forms.FloatField(label='Study hours per day', min_value=0.5, max_value=12, initial=1,
                                    widget=forms.NumberInput(attrs={'step': '0.5'}))

    def __init__(self, *args, requirements=(), **kwargs):
        super().__init__(*args, **kwargs)
        for index, requirement in enumerate(requirements):
            from .roadmap_graph import requirement_id
            reference = requirement.get('id') or requirement_id(requirement['skill'])
            self.fields[f'skill_{reference}'] = forms.BooleanField(label=requirement['skill'], required=False)

    def clean_hours_per_day(self):
        import math
        value = self.cleaned_data['hours_per_day']
        if not math.isfinite(value):
            raise forms.ValidationError('Enter a valid number of hours.')
        return value
