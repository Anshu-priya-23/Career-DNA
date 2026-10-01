import os
import json

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from .forms import StandardSignUpForm, UserProfileForm
from .models import RoadmapTask
from .ai_parser import parse_resume_with_ai, analyze_skill_gap


# 1. Handles new user registration
def signup_view(request):
    if request.method == 'POST':
        form = StandardSignUpForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Neural Profile initialized successfully. Please authenticate to continue."
            )
            return redirect('login')
    else:
        form = StandardSignUpForm()

    return render(request, 'registration/signup.html', {'form': form})


@login_required
def discovery_view(request):
    return render(request, 'accounts/discovery.html')


# 2. Handles profile updates, resume parsing, and gap analysis
@login_required
def profile_view(request):
    profile = request.user.profile
    analysis = {}                  # ADD THIS LINE
    missing_skills_list = None

    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=profile)

        if form.is_valid():
            profile_instance = form.save()

            # ---------- Resume Parsing ----------
            if 'resume' in request.FILES:
                try:
                    profile_instance.refresh_from_db()
                    file_path = profile_instance.resume.path

                    if os.path.exists(file_path):
                        parsed_data = parse_resume_with_ai(file_path)

                        profile_instance.skills = str(
                            parsed_data.get('skills', '')
                        )
                        profile_instance.save()

                        messages.success(
                            request,
                            "Resume parsed successfully."
                        )

                except Exception as e:
                    messages.error(
                        request,
                        f"Resume parsing failed: {e}"
                    )

            # ---------- Skill Gap Analysis ----------
            if 'analyze_gap' in request.POST:
                try:
                    if (
                        profile_instance.skills and
                        profile_instance.dream_role and
                        profile_instance.dream_company
                    ):

                        analysis = analyze_skill_gap(
                            profile_instance.skills,
                            profile_instance.dream_role,
                            profile_instance.dream_company
                        )

                        profile_instance.missing_skills = analysis.get(
                            "missing_skills",
                            ""
                        )

                        profile_instance.resume_score = analysis.get(
                            "resume_score",
                            ""
                        )

                        # Get roadmap steps from AI
                        study_steps = analysis.get(
                            "study_plan_steps",
                            []
                        )

                        # Keep old study_plan field for compatibility
                        profile_instance.study_plan = "\n".join(study_steps)

                        profile_instance.save()

                        # Delete previous roadmap tasks
                        RoadmapTask.objects.filter(
                            profile=profile_instance
                        ).delete()

                        # Save new roadmap tasks
                        for step in study_steps:
                            RoadmapTask.objects.create(
                                profile=profile_instance,
                                task_description=step
                            )

                        missing_skills_list = [
                            s.strip()
                            for s in profile_instance.missing_skills.split(",")
                            if s.strip()
                        ]

                        messages.success(
                            request,
                            "Skill Gap Analysis Completed!"
                        )

                    else:
                        messages.warning(
                            request,
                            "Please upload your resume and complete your profile first."
                        )

                except Exception as e:
                    messages.error(
                        request,
                        f"Analysis failed: {e}"
                    )

            return render(
                request,
                "accounts/profile.html",
                {
                    "form": form,
                    "profile": profile_instance,
                    "missing_skills_list": missing_skills_list,
                },
            )

    else:
        form = UserProfileForm(instance=profile)

    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "profile": profile,
            "missing_skills_list": None,
            'roadmap_data': analysis.get('roadmap', []), # Send the list to the HTML
        },
    )


@login_required
def update_task_view(request, task_id):
    """
    AJAX endpoint to update checklist task completion status.
    """
    if request.method == "POST":

        task = get_object_or_404(
            RoadmapTask,
            id=task_id,
            profile=request.user.profile
        )

        data = json.loads(request.body)

        task.is_completed = data.get(
            "completed",
            False
        )

        task.save()

        return JsonResponse({
            "status": "success"
        })

    return JsonResponse(
        {
            "status": "error"
        },
        status=400
    )