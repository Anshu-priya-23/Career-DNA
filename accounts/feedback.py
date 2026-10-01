"""Short, conditional edits grounded in extracted resume text and JD quotes."""
import re


def concise_edits(text, jd, requirements, corrections):
    from .services import supported, normalize
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    def anchor(section):
        for index, line in enumerate(lines):
            if section.casefold() in line.casefold() and len(line.split()) <= 5:
                return lines[index+1] if index+1 < len(lines) else line
        return ''
    eligibility = re.search(r'[^.\n]*(?:CGPA|GPA|marks|percentage|aggregate)[^.\n]*\d[^.\n]*|[^.\n]*\d+\s*%[^.\n]*(?:academic|education|degree|class|standard|aggregate)[^.\n]*', jd, re.I)
    marks_present = bool(re.search(r'\b(CGPA|GPA|marks|percentage)\b|\d+(?:\.\d+)?\s*%', text, re.I))
    edits, covered = [], set()
    if eligibility and not marks_present:
        before = anchor('Education')
        edits.append(dict(section='Education', issue='Show the academic eligibility detail requested by this JD.', before=before, after=(before + '; ' if before else '') + '[Your actual CGPA or marks, with the scale]', condition='Include this only if you have the verified result. JD: ' + eligibility.group(0).strip()))
    # Required gaps first; optional gaps after useful required edits.
    gaps = sorted([r for r in requirements if r['kind']=='skill' and r['status']!='demonstrated'], key=lambda r:r['optional'])
    project_skills = set()
    for r in gaps:
        if len(edits) >= 5: break
        if re.search(r'REST|API', r['skill'], re.I):
            before = next((line for line in lines if re.search(r'\b(built|developed|created|implemented)\b', line, re.I) and re.search(r'Spring\s*Boot|Django|Flask|FastAPI', line, re.I)), '')
            if before:
                framework = re.search(r'Spring\s*Boot|Django|Flask|FastAPI', before, re.I).group(0)
                edits.append(dict(section='Projects', skill=r['skill'], issue='This project does not clearly show ' + r['skill'] + '.', before=before, after='Built [' + r['skill'] + ' endpoints you actually implemented] using ' + framework + ' to [actual request/input] and return [actual response/output].', condition='Use this only if these endpoints were part of this project. Otherwise, learn the skill before claiming it.'))
                project_skills.add(normalize(r['skill']))
                covered.add(normalize(r['skill']))
    absent = [r for r in gaps if r['status']=='absent' and normalize(r['skill']) not in project_skills]
    if absent and len(edits)<5:
        labels = [r['skill'] for r in absent if not r['optional']][:5] or [r['skill'] for r in absent][:3]
        before = anchor('Skills')
        edits.append(dict(section='Skills', skill=', '.join(labels), issue='Missing relevant keywords: ' + ', '.join(labels) + '.', before=before, after=(before + ', ' if before else '') + '[' + ', '.join(labels) + ' - include only skills you already possess]', condition='Already know a skill? Add supporting project evidence too. Do not know it? Leave it out of your resume and include it in your learning plan.'))
        covered.update(normalize(label) for label in labels)
    for r in gaps:
        if normalize(r['skill']) in project_skills: continue
        if r['status']=='absent': continue
        if len(edits) >= 5: break
        skill = r['skill']
        section = 'Projects' if r['status']=='listed' else 'Skills'
        before = r.get('resume_quote') or anchor(section)
        if before and not supported(before,text): before = ''
        if section == 'Projects':
            after = 'Used ' + skill + ' to [specific task you actually completed]; [actual input/output or result].'
        else:
            after = (before + ', ' if before else '') + '[' + skill + ', only if you already know it]'
        edits.append(dict(section=section, skill=skill, issue=(skill + ' is listed, but its use is not demonstrated.') if r['status']=='listed' else (skill + ' has no supporting evidence in this resume.'), before=before, after=after, condition='Use this wording only if it accurately describes your knowledge or work.'))
        covered.add(normalize(skill))
    for c in corrections:
        if len(edits) >= 5: break
        row = c.model_dump() if hasattr(c,'model_dump') else c
        before = row.get('evidence','')
        if not before or not supported(before,text): continue
        section = next((s for s in ('Projects','Skills','Summary','Education') if s.casefold() in row['section'].casefold()),None)
        if not section: continue
        if re.search(r'cgpa|gpa|marks|percentage', str(row),re.I) and not eligibility: continue
        relevant = [r['skill'] for r in requirements if r['kind']=='skill' and supported(r['skill'],before)]
        if not relevant or any(normalize(s) in covered for s in relevant): continue
        # New facts are placeholders, never a generated achievement.
        skill = relevant[0]
        after = 'Used ' + skill + ' to [actual task or contribution]; [verified purpose or result].'
        signature = (section,normalize(before))
        if any((e['section'],normalize(e['before']))==signature for e in edits): continue
        edits.append(dict(section=section,skill=skill,issue='Make your contribution using ' + skill + ' clear.',before=before,after=after,condition='Replace the brackets with true details from this work; do not add invented metrics.'))
        covered.add(normalize(skill))
    return edits[:5], ('Include verified marks/CGPA because the JD explicitly requests academic eligibility.' if eligibility else 'This JD does not explicitly request marks or CGPA; you do not need to add them.')
