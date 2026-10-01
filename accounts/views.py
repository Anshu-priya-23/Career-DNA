import json
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, FileResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.views.decorators.http import require_POST
from .forms import StandardSignUpForm, UserProfileForm, RoadmapForm
from .models import RoadmapTask, UserProfile
from .ai_parser import extract_text_from_pdf, review_resume, plan_topics, AnalysisError
from .services import assess, schedule, learning_gaps
from .roadmap_graph import skill_key, grounded_catalog, requirement_id


def signup_view(request):
    form = StandardSignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profile created. Please sign in.')
        return redirect('login')
    return render(request, 'registration/signup.html', {'form': form})

@login_required
def discovery_view(request):
    return render(request, 'accounts/discovery.html')

@login_required
def profile_view(request):
    profile = request.user.profile
    catalog_assessment = dict(profile.assessment)
    if profile.assessment:
        catalog_assessment['requirements'] = grounded_catalog(profile.assessment, extract_text_from_pdf(profile.resume) if profile.resume else '')
    requirements = learning_gaps(catalog_assessment)
    form = UserProfileForm(instance=profile)
    initial = dict(profile.preparation)
    if 'days' not in initial and 'weeks' in initial:
        initial['days'] = max(1, round(float(initial['weeks']) * 7))
        initial['hours_per_day'] = float(initial.get('hours_per_week', 7)) / 7
    # Match confirmations by skill name, not by a previous review's field index.
    known_skills = initial.get('known_skills', [])
    initial = {key: value for key, value in initial.items() if not key.startswith('skill_')}
    initial.update({f'skill_{r["id"]}': skill_key(r['skill']) in {skill_key(s) for s in known_skills} for r in requirements})
    roadmap_form = RoadmapForm(requirements=requirements, initial=initial)
    if request.method == 'POST':
        action = request.POST.get('action', 'analyze' if 'analyze_gap' in request.POST else 'save')
        if action == 'roadmap':
            roadmap_form = RoadmapForm(request.POST, requirements=requirements)
            if roadmap_form.is_valid():
                try:
                    if not profile.assessment:
                        raise AnalysisError('Analyze your resume first.')
                    preferences = roadmap_form.cleaned_data
                    confirmations = {r['skill']: preferences[f'skill_{r["id"]}'] for r in requirements}
                    preferences['known_skills'] = [skill for skill, known in confirmations.items() if known] + [r['skill'] for r in profile.assessment.get('requirements', []) if r.get('kind') == 'skill' and r.get('status') == 'demonstrated']
                    known = {skill_key(s) for s in preferences['known_skills']}
                    preferences['known_requirement_ids'] = [r['id'] for r in catalog_assessment.get('requirements', []) if skill_key(r['skill']) in known]
                    planning_requirements = [r for r in requirements if skill_key(r['skill']) not in known]
                    if planning_requirements:
                        payload = {'role': profile.assessment['role'], 'jd': profile.assessment['jd'], 'gaps': planning_requirements, 'requirement_catalog': catalog_assessment['requirements'], 'known_requirement_ids': preferences['known_requirement_ids'], 'known_skills': preferences['known_skills'], 'preferences': preferences}
                        retained_plan = None
                        for attempt in range(2):
                            plan = plan_topics(payload)
                            if retained_plan is not None:
                                from .schemas import TopicPlan
                                remap = {t.id: 'completion_' + t.id for t in plan.topics}
                                additions = [dict(t.model_dump(), id=remap[t.id], prerequisites=[remap.get(dep,dep) for dep in t.prerequisites]) for t in plan.topics]
                                combined = TopicPlan(topics=[t.model_dump() for t in retained_plan.topics] + additions)
                                combined._model_used = plan._model_used
                                combined._fallback_used = plan._fallback_used
                                plan = combined
                            try:
                                if any(not t.requirement_id and t.topic_kind != 'unrelated' for t in plan.topics):
                                    raise AnalysisError('Every related task must reference a supplied requirement_id.', category='roadmap_graph')
                                roadmap = schedule(plan, dict(preferences, known_skills=preferences['known_skills'] + preferences['known_requirement_ids']), catalog_assessment['requirements'])
                                break
                            except AnalysisError as exc:
                                if exc.category != 'roadmap_graph' or attempt:
                                    raise
                                missing_ids = getattr(exc, 'missing_requirement_ids', [])
                                if missing_ids:
                                    retained_plan = plan
                                    payload['gaps'] = [r for r in planning_requirements if r['id'] in missing_ids]
                                    payload['existing_task_graph'] = plan.model_dump()
                                    payload['validation_feedback'] = 'Generate ONLY tasks for these missing gap IDs. The existing graph will be retained and merged. ' + str(exc)
                                    continue
                                # Give Gemini the exact invalid graph, not a vague user retry.
                                payload['graph_to_repair'] = plan.model_dump()
                                payload['validation_feedback'] = str(exc)
                        roadmap['model_used'] = plan._model_used
                    else:
                        roadmap = dict(phases=[], deferred=[], budget=preferences['days']*preferences['hours_per_day'], planned_hours=0, preferences=preferences, warning='You marked all identified skills as known. Add genuine evidence to your resume; no learning tasks are needed for these skills.')
                    roadmap['role'] = profile.assessment['role']
                    roadmap['jd'] = profile.assessment['jd']
                    with transaction.atomic():
                        locked = UserProfile.objects.select_for_update().get(pk=profile.pk)
                        if locked.assessment != profile.assessment:
                            raise AnalysisError('Assessment changed during generation. Reload and retry.')
                        locked.roadmap = roadmap
                        locked.assessment = catalog_assessment
                        locked.preparation = preferences
                        locked.save(update_fields=['roadmap', 'preparation', 'assessment'])
                        locked.roadmap_tasks.all().delete()
                        RoadmapTask.objects.bulk_create([RoadmapTask(profile=locked, task_description=f"Phase {p['number']}: {p['checkpoint']}"[:255]) for p in roadmap['phases']])
                    messages.success(request, 'Learning plan saved.' if not roadmap['deferred'] else 'Learning plan saved. Some topics need more time and are listed below.')
                    return redirect('profile')
                except AnalysisError as exc:
                    messages.error(request, str(exc))
        else:
            form = UserProfileForm(request.POST, request.FILES, instance=profile)
            if form.is_valid():
                try:
                    result = None
                    if action == 'analyze':
                        if not form.cleaned_data.get('dream_role'):
                            raise AnalysisError('Enter a target role; a company name alone is insufficient.')
                        resume = form.cleaned_data.get('resume')
                        if not resume:
                            raise AnalysisError('Upload a resume PDF first.')
                        text = extract_text_from_pdf(resume)
                        result = assess(text, form.cleaned_data['dream_role'], form.cleaned_data['job_description'], review_resume(text, form.cleaned_data['dream_role'], form.cleaned_data['job_description']))
                    with transaction.atomic():
                        saved = form.save(commit=False)
                        if result is not None:
                            saved.assessment = result
                        saved.save(update_fields=list(form.Meta.fields) + (['assessment'] if result is not None else []))
                    messages.success(request, 'Assessment saved.' if result is not None else 'Profile saved. Analyze to update your assessment.')
                    return redirect('profile')
                except AnalysisError as exc:
                    messages.error(request, str(exc))
        # ModelForm mutates its instance even when validation fails; reload saved results.
        profile = UserProfile.objects.get(pk=profile.pk)
    phases = [dict(p) for p in profile.roadmap.get('phases', [])]
    daily = float(profile.roadmap.get('preferences', {}).get('hours_per_day') or float(profile.roadmap.get('preferences', {}).get('hours_per_week', 7)) / 7)
    used = 0
    import math
    for phase in phases:
        phase.setdefault('start_day', math.floor(used / daily)+1)
        used += phase.get('total_hours', 0)
        phase.setdefault('end_day', max(phase['start_day'], math.ceil(used / daily)))
    edits = profile.assessment.get('edits', [])[:5]
    marks_notice = profile.assessment.get('marks_notice', '')
    if profile.assessment and 'edits' not in profile.assessment and profile.resume:
        from .feedback import concise_edits
        try:
            edits, marks_notice = concise_edits(extract_text_from_pdf(profile.resume), profile.assessment.get('jd', ''), profile.assessment.get('requirements', []), profile.assessment.get('corrections', []))
        except AnalysisError:
            pass
    return render(request, 'accounts/profile.html', {'form': form, 'profile': profile, 'assessment': profile.assessment, 'edits': edits, 'marks_notice': marks_notice, 'gaps': learning_gaps(profile.assessment), 'roadmap_form': roadmap_form, 'plan_open': request.GET.get('plan') == '1' or (request.method == 'POST' and request.POST.get('action') == 'roadmap'), 'roadmap': profile.roadmap, 'plan_stale': bool(profile.roadmap) and (profile.roadmap.get('jd') != profile.assessment.get('jd') or profile.roadmap.get('role') != profile.assessment.get('role')), 'phase_tasks': list(zip(phases, profile.roadmap_tasks.order_by('id')))})

@login_required
@require_POST
def update_task_view(request, task_id):
    task = get_object_or_404(RoadmapTask, id=task_id, profile=request.user.profile)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or type(data.get('completed')) is not bool:
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'completed must be a JSON boolean.'}, status=400)
    task.is_completed = data['completed']
    task.save(update_fields=['is_completed'])
    return JsonResponse({'status': 'success', 'completed': task.is_completed})

@login_required
def resume_view(request):
    resume = request.user.profile.resume
    if not resume:
        from django.http import Http404
        raise Http404
    return FileResponse(resume.open('rb'), content_type='application/pdf', as_attachment=True, filename='resume.pdf')
