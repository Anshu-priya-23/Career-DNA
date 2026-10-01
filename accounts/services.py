"""Conservative, versioned scoring and budget scheduling, independent of Gemini."""
import math
import json
import re
import unicodedata
from .ai_parser import AnalysisError, logger
from .resources import resources_for


def normalize(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('\u00ad', '')
    value = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', value)
    return ' '.join(re.findall(r'\w+|[+#]', value))


def supported(quote, source):
    q = normalize(quote)
    return bool(q) and (' ' + q + ' ') in (' ' + normalize(source) + ' ')


def learning_gaps(assessment):
    from .roadmap_graph import skill_key
    rows = assessment.get('requirements', [])
    seen = {skill_key(r['skill']) for r in rows if r.get('kind') == 'skill' and r.get('status') == 'demonstrated'}
    gaps = []
    for row in sorted(rows, key=lambda r: r.get('optional', False)):
        key = skill_key(row['skill'])
        if row.get('kind') == 'skill' and row.get('status') != 'demonstrated' and key not in seen:
            seen.add(key)
            gaps.append(row)
    return gaps


def assess(text, role, jd, review):
    warnings, requirements, seen = [], [], set()
    for item in sorted(review.requirements, key=lambda item: item.optional):
        r = item.model_dump()
        if not jd or not supported(r['requirement_quote'], jd):
            warnings.append('Excluded an ungrounded job requirement: ' + r['skill'])
            continue
        if r['kind'] == 'skill' and not supported(r['skill'], r['requirement_quote']):
            warnings.append('Excluded a skill label not grounded in its JD quote: ' + r['skill'])
            continue
        from .roadmap_graph import skill_key
        key = skill_key(r['skill'])
        if key in seen:
            continue
        seen.add(key)
        evidence = supported(r['resume_quote'], text)
        if r['kind'] == 'experience' and re.search(r'freshers?\s+(accepted|welcome|eligible)|no\s+(prior\s+)?experience\s+required', r['requirement_quote'], re.I):
            continue
        # Action + skill + substantial exact quotation is necessary, not a proficiency guarantee.
        action = re.search(r'\b(built|developed|implemented|created|designed|tested|deployed|analyzed|analysed|automated|maintained|led|optimized|secured|trained)\b', normalize(r['resume_quote']))
        demonstrated = evidence and r['demonstrated'] and (r['kind'] != 'skill' or (action and supported(r['skill'], r['resume_quote']) and len(normalize(r['resume_quote']).split()) >= 6))
        if r['kind'] == 'education':
            demonstrated = demonstrated and supported(r['skill'], r['resume_quote'])
        if r['kind'] == 'experience':
            needed = re.search(r'(\d+)\s*\+?\s*years?', r['requirement_quote'], re.I)
            actual = re.search(r'(\d+)\s*\+?\s*years?', r['resume_quote'], re.I)
            demonstrated = demonstrated and bool(needed and actual and int(actual[1]) >= int(needed[1]))
        if r['resume_quote'] and not evidence:
            warnings.append('Unsupported resume quote discarded for ' + r['skill'])
            r['resume_quote'] = ''
        status = 'demonstrated' if demonstrated else ('listed' if supported(r['skill'], text) or evidence else 'absent')
        r.update(status=status, group='optional' if r['optional'] else status,
                 gap_type='resume evidence' if status == 'listed' else ('unknown knowledge/practical experience; confirm below' if status == 'absent' else 'supported evidence'),
                 points={'demonstrated': 100, 'listed': 25, 'absent': 0}[status])
        requirements.append(r)
    if jd.strip() and not requirements:
        raise AnalysisError('No verifiable JD requirements were extracted. Clarify the JD and retry; previous results are preserved.')
    from .roadmap_graph import grounded_catalog
    requirements = grounded_catalog({'jd': jd, 'requirements': requirements}, text)
    corrections = []
    for c in review.corrections:
        row = c.model_dump()
        if row['evidence'] and not supported(row['evidence'], text):
            warnings.append('A correction with an unsupported quotation was excluded.')
            continue
        wording = row['suggested_wording']
        if wording and not supported(wording, text):
            # Do not trust even a partly placeholder-filled generated achievement.
            row['suggested_wording'] = ((row['evidence'] + '\n') if row['evidence'] else '') + '[Your actual contribution or relevant detail for ' + row['section'] + ']; [tools you actually used]; [verified outcome, if available].'
        corrections.append(row)
    sections = {
        'Education': bool(re.search(r'\b(education|academic|university|college|bachelor|master|degree)\b', text, re.I)),
        'Skills': bool(re.search(r'\b(skills|technologies|technical)\b', text, re.I)),
        'Projects or experience': bool(re.search(r'\b(projects?|experience|internship|employment)\b', text, re.I)),
    }
    contact = bool(re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text))
    action_lines = [line.strip() for line in text.splitlines() if re.search(r'\b(built|developed|implemented|created|designed|tested|deployed|analyzed|automated|maintained|led)\b', line, re.I)]
    clear_lines = [line for line in action_lines if len(line.split()) >= 8]
    outcomes = [line for line in clear_lines if re.search(r'\b(result|reduced|improved|enabled|users|accuracy|saved)\b|\d+\s*%', line, re.I)]
    criteria = [
        ('Identifiable sections', 30, round(30 * sum(sections.values()) / 3), ', '.join(k for k, v in sections.items() if v) or 'No recognized headings'),
        ('Contact information', 10, 10 if contact else 0, 'Email detected' if contact else 'No email detected'),
        ('Action and context', 40, min(40, len(clear_lines) * 10), '\n'.join(clear_lines[:4]) or 'No substantial action descriptions detected'),
        ('Explained outcomes', 20, min(20, len(outcomes) * 10), '\n'.join(outcomes[:2]) or 'No outcomes detected; qualitative results are acceptable'),
    ]
    rubric = [dict(criterion=n, weight=w, earned=p, deduction=w-p, evidence=e) for n,w,p,e in criteria]
    for section, present in sections.items():
        if not present:
            corrections.append(dict(section=section, priority='high', issue='No recognizable ' + section + ' section.', why='Reviewers need to locate relevant facts quickly.', correction='Add a clear heading and genuine details. If you have no work experience, document projects; gain experience through a project rather than inventing employment.', evidence='', suggested_wording='[Section heading]\n[Your genuine details]'))
    if not clear_lines:
        corrections.append(dict(section='Projects / experience', priority='high', issue='Descriptions lack an identifiable action and context.', why='A technology list does not demonstrate practical work.', correction='Explain what you built, your contribution, tools used, and actual outcome. Add metrics only if verified.', evidence='', suggested_wording='Built [actual deliverable] using [tools actually used] to [purpose]; [verified outcome].'))
    elif not outcomes:
        corrections.append(dict(section='Projects / experience', priority='medium', issue='Explain the outcome of your work.', why='The reader can see an action but not its result.', correction='For a relevant project, explain what worked or what the project enabled. Add numbers only if you can verify them; a clear qualitative result is enough.', evidence=clear_lines[0], suggested_wording=clear_lines[0] + '\n[What this enabled or improved, if you can verify it].'))
    corrections.sort(key=lambda c: {'high': 0, 'medium': 1, 'low': 2}[c['priority']])
    required = [r for r in requirements if not r['optional']]
    from .feedback import concise_edits
    edits, marks_notice = concise_edits(text, jd, requirements, review.corrections)
    return dict(version=1, role=role, jd=jd, model_used=review._model_used,
                edits=edits, marks_notice=marks_notice,
                provider_notice='The primary model was unavailable; this assessment used the configured fallback model.' if review._fallback_used else '',
                general=not bool(jd.strip()), quality_score=sum(r['earned'] for r in rubric),
                job_fit=round(sum(r['points'] for r in required)/len(required)) if required else None,
                rubric=rubric, requirements=requirements, corrections=corrections, warnings=warnings,
                limitations='Text-only heuristic rubric; visual layout and proficiency are not verified. Job fit averages explicit required items equally: demonstrated 100, listed 25, absent 0. Optional items do not affect fit. Scores are not hiring probabilities. The 90% accuracy target is unverified.')


def schedule(plan, preferences, requirements):
    logger.info('Roadmap graph: %s', json.dumps({'allowed': [r['skill'] for r in requirements], 'known': preferences.get('known_skills', []), 'topics': [{'id': t.id, 'title': t.title, 'requirement': t.requirement, 'prerequisites': t.prerequisites} for t in plan.topics]}))
    from .roadmap_graph import resolve_graph
    topics = resolve_graph(plan, requirements, preferences.get('known_skills', []))
    from .roadmap_graph import requirement_id, skill_key
    id_mode = any(t.requirement_id for t in plan.topics)
    known = set(preferences.get('known_skills', []))
    known_keys = {skill_key(s) for s in known}
    allowed = {r['skill'] for r in requirements if skill_key(r['skill']) not in known_keys and requirement_id(r['skill'], r.get('kind', 'skill')) not in known and r.get('kind', 'skill') == 'skill'}
    ordered, visiting, visited = [], set(), set()
    def visit(key):
        if key in visiting:
            raise AnalysisError(f'Circular prerequisites at task {key!r}.', category='roadmap_graph')
        if key in visited:
            return
        visiting.add(key)
        for dependency in topics[key]['prerequisites']:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
        ordered.append(topics[key])
    priority = {r['skill']: r.get('optional', False) for r in requirements}
    id_priority = {}
    for r in requirements:
        reference = requirement_id(r['skill'],r.get('kind','skill'))
        id_priority[reference] = id_priority.get(reference,True) and r.get('optional',False)
    for key in sorted(topics, key=lambda k: id_priority[topics[k]['requirement_id']] if id_mode else priority[topics[k]['requirement']]):
        visit(key)
    if id_mode:
        required_ids = {requirement_id(skill) for skill in allowed}
        missing_ids = required_ids - {t['requirement_id'] for t in topics.values() if t['topic_kind']=='target'}
        missing = {r['skill'] for r in requirements if requirement_id(r['skill'],r.get('kind','skill')) in missing_ids}
    else:
        missing = allowed - {topic['requirement'] for topic in topics.values()}
    if missing:
        error = AnalysisError('Selected skills missing from graph: ' + ', '.join(sorted(missing)), category='roadmap_graph')
        error.missing_requirement_ids = [requirement_id(r['skill'],r.get('kind','skill')) for r in requirements if r['skill'] in missing]
        raise error
    daily = float(preferences['hours_per_day']) if 'hours_per_day' in preferences else float(preferences['hours_per_week']) / 7
    days = float(preferences['days']) if 'days' in preferences else float(preferences['weeks']) * 7
    weekly = daily * 7
    budget = math.floor(days * daily * 100) / 100
    used, included, phases, deferred = 0, set(), [], []
    for t in ordered:
        total = sum(t[k] for k in ['learning_hours', 'practice_hours', 'revision_hours', 'assessment_hours'])
        if used + total > budget or any(p not in included for p in t['prerequisites']):
            deferred.append({'title': t['title'], 'hours': total, 'reason': 'Insufficient time or prerequisite deferred.'})
            continue
        t.update(total_hours=total, start_week=math.floor(used / weekly)+1, end_week=math.ceil((used+total)/weekly),
                 resources=resources_for(t, preferences), number=len(phases)+1)
        t.update(start_day=math.floor(used / daily)+1, end_day=math.ceil((used+total)/daily))
        used += total
        included.add(t['id'])
        phases.append(t)
    return dict(phases=phases, deferred=deferred, budget=budget, planned_hours=used, unused_hours=round(budget-used,2), preferences=preferences,
                warning='These are study estimates. Completing this plan does not guarantee job fit or selection. Deferred topics need more time.')
