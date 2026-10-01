"""Resolve model topic labels to a conservative, grounded dependency graph."""
import re
import hashlib
from .ai_parser import AnalysisError, logger


def skill_key(value):
    value = re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).strip()
    aliases = {'rest api development': 'rest api', 'rest apis': 'rest api', 'restful apis': 'rest api', 'restful api': 'rest api', 'rest api design': 'rest api', 'springboot': 'spring boot', 'python programming': 'python', 'javascript programming': 'javascript', 'js': 'javascript', 'postgresql': 'postgresql', 'postgres': 'postgresql', 'react js': 'react', 'reactjs': 'react', 'git version control': 'git', 'version control with git': 'git', 'version control': 'git', 'version control using git': 'git', 'structured query language': 'sql', 'sql databases': 'sql'}
    return aliases.get(value, value)


def requirement_id(skill, kind='skill'):
    return 'req_' + hashlib.sha256((kind + ':' + skill_key(skill)).encode()).hexdigest()[:12]


def with_requirement_ids(rows):
    return [dict(row, id=requirement_id(row['skill'], row.get('kind','skill'))) for row in rows]


def grounded_catalog(assessment, text):
    """Recover explicit JD skills that the model omitted; never infer extra JD skills."""
    from .services import supported
    rows = with_requirement_ids(assessment.get('requirements', []))
    seen = {skill_key(row['skill']) for row in rows}
    patterns = {'Git':r'\bGit\b|\bversion control\b', 'Java':r'\bJava\b', 'Spring Boot':r'\bSpring\s*Boot\b', 'React':r'\bReact(?:\.js|JS)?\b', 'PostgreSQL':r'\bPostgreSQL\b|\bPostgres\b', 'SQL':r'\bSQL\b', 'JavaScript':r'\bJavaScript\b', 'HTML':r'\bHTML5?\b', 'CSS':r'\bCSS3?\b', 'HTTP':r'\bHTTP\b', 'JUnit':r'\bJUnit\b', 'JWT':r'\bJWT\b', 'Docker':r'\bDocker\b', 'REST APIs':r'\bREST(?:ful)?\s+APIs?\b'}
    jd = assessment.get('jd', '')
    for label, pattern in patterns.items():
        match = re.search(pattern, jd, re.I)
        if not match or skill_key(label) in seen: continue
        # Use the exact JD token/phrase, keeping the stored quote verifiable.
        quote = match.group(0)
        skill = quote
        if not supported(quote,jd): continue
        present = re.search(pattern,text,re.I)
        start = max(jd.rfind('\n',0,match.start()), jd.rfind('.',0,match.start()))+1
        ends = [position for position in (jd.find('\n',match.end()),jd.find('.',match.end())) if position>=0]
        clause = jd[start:min(ends) if ends else len(jd)]
        optional = bool(re.search(r'preferred|optional|nice to have|bonus',clause,re.I))
        rows.append(dict(id=requirement_id(skill),skill=skill,kind='skill',optional=optional,requirement_quote=quote,resume_quote=present.group(0) if present else '',demonstrated=False,status='listed' if present else 'absent',group='optional' if optional else ('listed' if present else 'absent'),points=25 if present else 0,explanation='Explicitly requested in the job description.',demonstration='Show a genuine project using this skill, or learn it first.'))
        seen.add(skill_key(skill))
    return rows


# A foundation is admitted only for a related target, never just because the
# provider attaches it to a target. This keeps unrelated skills excluded.
FOUNDATIONS = {
    'git': ({'react', 'spring boot', 'django', 'rest api', 'docker', 'java', 'python'}, 'Git project workflow', 'Commit project changes and practise branching and merging.'),
    'http': ({'rest api', 'spring boot', 'django', 'react'}, 'HTTP and JSON basics', 'Send a sample HTTP request and explain its method, status and JSON body.'),
    'json': ({'rest api', 'spring boot', 'django', 'react'}, 'JSON basics', 'Create and validate a small JSON request and response.'),
    'java': ({'spring boot'}, 'Java foundations', 'Write a Java class with methods and a small test.'),
    'javascript': ({'react', 'node js', 'nodejs'}, 'JavaScript foundations', 'Practise functions, arrays and asynchronous requests in a small script.'),
    'python': ({'django', 'flask', 'fastapi'}, 'Python foundations', 'Write and test functions that transform a small input dataset.'),
    'sql': ({'postgresql', 'mysql', 'sql'}, 'SQL foundations', 'Create two related tables and practise SELECT and JOIN queries.'),
    'html': ({'react', 'css'}, 'HTML foundations', 'Build an accessible form with labels and inputs.'),
    'css': ({'react'}, 'CSS foundations', 'Style a simple form to work on a narrow screen.'),
}


def foundation_key(value):
    value = skill_key(value)
    value = re.sub(r'\b(basics|basic|foundations|foundation|fundamentals|programming|introduction|intro|to)\b', '', value)
    return ' '.join(value.split())


def resolve_graph(plan, requirements, known_skills=()):
    if any(t.requirement_id for t in plan.topics):
        return resolve_id_graph(plan, requirements, known_skills)
    topics = {t.id.strip(): t.model_dump() for t in plan.topics}
    if len(topics) != len(plan.topics):
        raise AnalysisError('Duplicate learning task identifiers.', category='roadmap_graph')
    allowed = {skill_key(r['skill']): r['skill'] for r in requirements}
    known = {skill_key(s) for s in known_skills}
    # Match references by ID or unambiguous title (models sometimes emit titles).
    references = {}
    for key, topic in topics.items():
        for label in (key, topic['title']):
            references.setdefault(skill_key(label), set()).add(key)
    repaired = []
    for key, topic in topics.items():
        target = skill_key(topic['requirement'])
        if target in allowed:
            topic['requirement'] = allowed[target]
        elif target in known:
            topic['_known'] = True
        else:
            base = foundation_key(topic['requirement'])
            related = [label for canonical, label in allowed.items() if base in FOUNDATIONS and canonical in FOUNDATIONS[base][0]]
            if not related:
                logger.warning('Roadmap invalid topic: id=%s reason=unrelated_requirement', key)
                raise AnalysisError(f'Task {key!r} has an unrelated requirement {topic["requirement"]!r}.', category='roadmap_graph')
            topic['requirement'] = related[0]
            repaired.append({'id':key,'reason':'related_foundation','requirement':related[0]})
    for key, topic in list(topics.items()):
        dependencies = []
        for reference in topic['prerequisites']:
            if skill_key(reference) in known or foundation_key(reference) in known:
                repaired.append({'id':key,'reason':'known_prerequisite','reference':reference})
                continue
            resolved = reference.strip() if reference.strip() in topics else None
            matches = references.get(skill_key(reference), set())
            if resolved is None and len(matches) == 1:
                resolved = next(iter(matches))
            if resolved is not None:
                if not topics[resolved].get('_known'):
                    dependencies.append(resolved)
                continue
            base = foundation_key(reference)
            target = skill_key(topic['requirement'])
            if base not in FOUNDATIONS or target not in FOUNDATIONS[base][0]:
                logger.warning('Roadmap invalid dependency: id=%s reference=%s', key, reference)
                raise AnalysisError(f'Task {key!r} references missing prerequisite {reference!r}.', category='roadmap_graph')
            generated_id = 'foundation_' + base
            if generated_id in topics and not topics[generated_id].get('_generated'):
                raise AnalysisError('Conflicting foundation task identifier.', category='roadmap_graph')
            if generated_id not in topics:
                _, title, exercise = FOUNDATIONS[base]
                topics[generated_id] = dict(id=generated_id, title=title, requirement=topic['requirement'], prerequisites=[], objectives=title, learning_hours=2, practice_hours=3, revision_hours=1, assessment_hours=1, exercise=exercise, project=exercise, checkpoint='Complete the exercise independently and explain the result.', _generated=True)
            dependencies.append(generated_id)
            repaired.append({'id':key,'reason':'added_foundation','reference':reference})
        topic['prerequisites'] = list(dict.fromkeys(dependencies))
    logger.info('Roadmap graph repairs: %s', repaired)
    return {key: topic for key, topic in topics.items() if not topic.get('_known')}


def resolve_id_graph(plan, requirements, known_skills=()):
    catalog = {r['id']:r for r in with_requirement_ids(requirements)}
    known = {s for s in known_skills if s.startswith('req_')} | {requirement_id(s) for s in known_skills if not s.startswith('req_')}
    topics = {t.id:t.model_dump() for t in plan.topics}
    if len(topics)!=len(plan.topics) or any(not k.strip() for k in topics):
        raise AnalysisError('Duplicate or empty task ID.',category='roadmap_graph')
    removed = set()
    for key,t in topics.items():
        reference = t['requirement_id']
        if t['topic_kind']=='unrelated':
            if reference:
                raise AnalysisError(f'Unrelated task {key!r} must not claim a target ID.',category='roadmap_graph')
            removed.add(key);continue
        if reference not in catalog:
            raise AnalysisError(f'Task {key!r} references unknown requirement ID {reference!r}.',category='roadmap_graph')
        t['requirement'] = catalog[reference]['skill']
        if reference in known:
            removed.add(key);continue
        if t['topic_kind']=='foundation':
            base = foundation_key(t['foundation_skill'])
            if requirement_id(base) in known or base in {skill_key(s) for s in known_skills}:
                t['_satisfied'] = True
                removed.add(key)
                continue
            target = skill_key(catalog[reference]['skill'])
            if base not in FOUNDATIONS or target not in FOUNDATIONS[base][0]:
                # Unrelated, declared foundation: exclude it, never accept it blindly.
                removed.add(key)
                logger.info('Excluded unrelated foundation: task=%s target_id=%s',key,reference)
    for key,t in topics.items():
        deps=[]
        for ref in t['prerequisites']:
            if ref in known: continue
            if ref in catalog:
                candidates=[k for k,row in topics.items() if row['requirement_id']==ref and row['topic_kind']=='target' and k not in removed and k!=key]
                if not candidates:
                    raise AnalysisError(f'Task {key!r} has unsatisfied requirement prerequisite {ref!r}.',category='roadmap_graph')
                deps.extend(candidates)
            elif ref in topics:
                if ref in removed:
                    # Known targets are satisfied; removed unrelated work is not.
                    if topics[ref]['requirement_id'] not in known and not topics[ref].get('_satisfied'):
                        removed.add(key)
                else: deps.append(ref)
            else:
                raise AnalysisError(f'Task {key!r} references unknown prerequisite {ref!r}.',category='roadmap_graph')
        t['prerequisites']=list(dict.fromkeys(deps))
    # Exclude dependants of unrelated removals, never schedule them out of order.
    changed=True
    while changed:
        changed=False
        for key,t in topics.items():
            if key not in removed and any(dep in removed for dep in t['prerequisites']):
                removed.add(key);changed=True
    logger.info('ID graph excluded tasks: %s',sorted(removed))
    return {k:t for k,t in topics.items() if k not in removed}
