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
        fields = ['college', 'degree', 'graduation_year', 'dream_company', 'dream_role', 'github_url', 'linkedin_url', 'resume']
        
        widgets = {
            'college': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Stanford University'}),
            'degree': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. B.Tech Computer Science'}),
            'graduation_year': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 2027'}),
            'dream_company': forms.Select(attrs={'class': 'w-full'}),
            'dream_role': forms.Select(attrs={'class': 'w-full'}),
            'github_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://github.com/...'}),
            'linkedin_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://linkedin.com/in/...'}),
            'resume': forms.FileInput(attrs={'class': 'form-control'}),
        }