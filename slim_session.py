import json
d = json.load(open('session_personal.json'))
slim = {'cookies': d['cookies'], 'origins': []}
json.dump(slim, open('session_personal.json', 'w'), indent=2)
print('OK', len(slim['cookies']), '件')
