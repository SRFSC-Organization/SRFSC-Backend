"""SRFSC interest questionnaire and validation (no database or network access)."""

QUIZ_VERSION = 1
QUESTIONS = [
    {'id': 'participation', 'label': 'How would you like to participate?', 'multiple': False,
     'options': [{'value': 'resident', 'label': 'Resident — learn and prepare my household'},
                 {'value': 'volunteer', 'label': 'Volunteer — help my community'}]},
    {'id': 'interests', 'label': 'What interests you? Choose at least one.', 'multiple': True,
     'options': [{'value': 'home', 'label': 'Home and yard preparedness'},
                 {'value': 'events', 'label': 'Workshops and community events'},
                 {'value': 'outreach', 'label': 'Neighborhood outreach'},
                 {'value': 'projects', 'label': 'Community volunteer projects'}]},
    {'id': 'experience', 'label': 'How familiar are you with community fire preparedness?', 'multiple': False,
     'options': [{'value': 'new', 'label': 'I am just getting started'},
                 {'value': 'learning', 'label': 'I have taken some preparation steps'},
                 {'value': 'experienced', 'label': 'I have participated in community activities'}]},
    {'id': 'availability', 'label': 'When would you prefer to participate?', 'multiple': False,
     'options': [{'value': 'weekdays', 'label': 'Weekdays'},
                 {'value': 'weekends', 'label': 'Weekends'},
                 {'value': 'flexible', 'label': 'Flexible'},
                 {'value': 'self-paced', 'label': 'At my own pace'}]},
    {'id': 'goal', 'label': 'What would you like to do first?', 'multiple': False,
     'options': [{'value': 'prepare', 'label': 'Explore household preparation resources'},
                 {'value': 'connect', 'label': 'Meet neighbors at an event'},
                 {'value': 'help', 'label': 'Find a volunteer opportunity'}]},
]


def validate_answers(answers):
    if not isinstance(answers, dict):
        raise ValueError('answers must be an object containing all five quiz answers.')
    if set(answers) != {question['id'] for question in QUESTIONS}:
        raise ValueError('Please answer all five questions using the current quiz.')
    cleaned = {}
    for question in QUESTIONS:
        key = question['id']
        allowed = {option['value'] for option in question['options']}
        value = answers[key]
        if question['multiple']:
            if (not isinstance(value, list) or not 1 <= len(value) <= len(allowed)
                    or any(not isinstance(item, str) or item not in allowed for item in value)):
                raise ValueError(f'{key}: choose at least one valid option.')
            cleaned[key] = list(dict.fromkeys(value))
        else:
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f'{key}: choose a valid option.')
            cleaned[key] = value
    return cleaned


def recommendations(answers):
    """Suggest existing pages from interests and the user's first goal."""
    if not answers:
        return []
    paths = {'prepare': ('Preparation resources', 'prepare/'),
             'connect': ('Upcoming community events', 'events/'),
             'help': ('Volunteer opportunities', 'get-involved/')}
    keys = [answers['goal']]
    for interest in answers['interests']:
        keys.append({'home': 'prepare', 'events': 'connect',
                     'outreach': 'help', 'projects': 'help'}[interest])
    return [{'title': paths[key][0], 'path': paths[key][1]} for key in dict.fromkeys(keys)]
