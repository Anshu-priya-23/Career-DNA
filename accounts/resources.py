"""Small curated catalog. No model-provided URLs are ever rendered."""
from urllib.parse import urlencode
import re


def resources_for(topic, preferences):
    language = preferences.get('resource_language', 'English')
    query = topic['title'] + ' ' + language
    allowance = topic['learning_hours']
    guidance = f'Select only relevant sections within the {allowance} learning-hour budget; reading replaces video time. Full-course completion is not assumed.'
    title = topic['title'].lower()
    selected = None
    if re.search(r'foundation|basic|function|variable|loop|introduction|fundamental', title):
        selected = ('Functions, Variables; Conditionals; Loops', 'Chapters 1-4: Python Basics, if-else and Flow Control, Loops, Functions')
    elif re.search(r'test|debug', title):
        selected = ('Unit Tests', 'Chapter 5: Debugging')
    elif re.search(r'file|csv', title):
        selected = ('File I/O', 'Chapter 10: Reading and Writing Files')
    if selected and 'python' in (topic['title'] + ' ' + topic['requirement']).lower() and language.lower() == 'english':
        return [dict(kind='YouTube playlist', title="CS50's Introduction to Programming with Python (CS50P) 2022", creator='CS50 / David J. Malan', url='https://www.youtube.com/playlist?list=PLhQjrBD2T3817j24-GogXmWqO5Q5vYy0V', verified=True, source='https://cs50.harvard.edu/python/', checked='2026-10-01', language='English', duration='Not verified', assignment=selected[0], why='Beginner-friendly instruction and practical problem sets.', workload=guidance),
                dict(kind='Book (optional alternative)', title='Automate the Boring Stuff with Python, 3rd edition', creator='Al Sweigart', url='https://automatetheboringstuff.com/', verified=True, source='https://automatetheboringstuff.com/', checked='2026-10-01', language='English', duration='Reading time varies', assignment=selected[1], why='Practical Python exercises; use as an alternative to video.', workload=guidance)]
    return [dict(kind=kind, title='Search for ' + topic['title'], creator='Not verified', url=base + urlencode({param: query + suffix}), verified=False, source='', checked='', language='Requested: ' + language + '; not verified', duration='Not verified', assignment='Choose lessons/chapters matching: ' + topic['objectives'], why='No verified catalog match. Check title, creator, language, scope and duration before using.', workload=guidance) for kind,base,param,suffix in [('YouTube search (unverified)', 'https://www.youtube.com/results?', 'search_query', ' course'), ('Book search (unverified)', 'https://www.google.com/search?', 'q', ' book official publisher')]]
