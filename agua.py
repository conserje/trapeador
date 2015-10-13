import os
import subprocess
import csv
import json
import requests
import time
import shutil
import re
import tempfile
import time
from requests.auth import HTTPBasicAuth

devnull = open(os.devnull)

print(time.strftime('%d %b %Y %H:%M:%S'))

resultrows = []

if os.path.isfile('user.txt'):
    with open('user.txt') as userfile:
        user = userfile.read()[:-1]
else:
    print('Couldn\'t find user.txt')
    exit(1)

if os.path.isfile('token.txt'):
    with open('token.txt') as tokenfile:
        token = tokenfile.read().rstrip()
else:
    if os.path.isfile('pass.txt'):
        with open('pass.txt') as passfile, open('id.txt') as idfile, open('secret.txt') as secretfile:
            ghdata = {
                'scopes': ['repo:status', 'delete_repo'],
                'note': 'Conserje API token',
                'client_id': idfile.read().rstrip(),
                'client_secret': secretfile.read().rstrip()
            }
            ghreq = requests.post('https://api.github.com/authorizations', data=json.dumps(ghdata), auth=HTTPBasicAuth(user, passfile.read()[:-1]))
            print(ghreq.text)
        if ghreq.status_code == 201:
            token = ghreq.json()['token']
            with open('token.txt', 'w') as tokenfile:
                tokenfile.write(token)
        else:
            print('GitHub API error')
            exit(1)
    else:
        print('Couldn\'t find token.txt, then couldn\'t find pass.txt, id.txt and secret.txt')
        exit(1)

if os.path.isfile('status.csv'):
    csvfile = open('status.csv', 'rb')
    reader = csv.reader(csvfile)
else:
    print('Couldn\'t find status.csv')
    exit(1)

tokenheader = {'Authorization': 'token ' + token}

def gettracked():
    result = []
    for thing in subprocess.check_output(['hub', 'ls-tree', '--full-tree', '-r', 'HEAD']).split('\n'):
        thing = thing[thing.rfind('\t') + 1:]
        if thing and os.path.isfile(thing):
            result.append(thing)
    return result

def clean():
    changed = False
    whitespace = re.compile(r'^\s*?(\r)?$')
    for tracked in gettracked():
        if not (tracked.endswith('.png') or tracked.endswith('.jpg') or tracked.endswith('.jpeg')):
            if not changed:
                shutil.copyfile(tracked, tracked + '.bk')
            with tempfile.TemporaryFile('r+a') as temp:
                with open(tracked, 'r') as orig:
                    first = True
                    blanklines = 0
                    crt = False
                    for line in orig:
                        match = whitespace.match(line)
                        if match:
                            line = '\n' if not match.group(1) else '\r\n'
                        if line == '\n':
                            blanklines += 1
                        elif line == '\r\n':
                            blanklines += 1
                            crt = True
                        else:
                            if blanklines and not first:
                                for _ in range(blanklines):
                                    temp.write('\r\n' if crt else '\n')
                            temp.write(line)
                            blanklines = 0
                            first = False
                            crt = False
                    if blanklines:
                        temp.write('\n')
                temp.seek(0)
                with open(tracked, 'w') as orig:
                    orig.write(temp.read())
            if not changed:
                changed = subprocess.call(['diff', tracked, tracked + '.bk'], stdout=devnull, stderr=devnull) != 0
                os.remove(tracked + '.bk')
    return changed

for row in reader:
    print(row[0])
    toclean = True
    checkhash = False
    owner = row[0][:row[0].find('/')]
    repo = row[0][row[0].find('/') + 1:]
    if len(row) != 1:
        if row[1] == 'pr':
            toclean = False
            prmerged = requests.get('https://api.github.com/repos/' + row[0] + '/pulls/' + row[2] + '/merge')
            if prmerged.status_code == 204:
                print('PR merged, deleting fork and rechecking')
                requests.delete('https://api.github.com/repos/' + user + '/' + repo, headers=tokenheader)
                toclean = True
            else:
                prinfo = requests.get('https://api.github.com/repos/' + row[0] + '/pulls/' + row[2], headers=tokenheader)
                if prinfo.status_code == 200:
                    prinfodict = prinfo.json()
                    if prinfodict['state'] == 'closed':
                        print('PR closed')
                    else:
                        prstatus = requests.get(prinfodict['statuses_url'], headers=tokenheader)

                        for status in prstatus.json():
                            if status['state'] == 'error' or status['state'] == 'failure':
                                print('CI failed, closing PR')
                                requests.patch('https://api.github.com/repos/' + row[0] + '/pulls/' + row[2], data={'state': 'closed'}, headers=tokenheader)
                                break

                else:
                    print('GitHub API error')
                    resultrows.append(row)
        if row[1] == 'hash':
            checkhash = True
            hashagainst = row[2]
    if toclean:
        subprocess.call(['hub', 'clone', row[0]])
        os.chdir(repo)
        currenthash = subprocess.check_output(['hub', 'log', '-n', '1', '--pretty=format:"%H"'])[1:-1]
        if not checkhash or currenthash != hashagainst:
            if clean():
                subprocess.call(['hub', 'fork'])
                subprocess.call(['hub', 'remote', 'set-url', 'origin', 'git@github.com:' + user + '/' + repo + '.git'])
                subprocess.call(['hub', 'add', '.'])
                subprocess.call(['hub', 'commit', '-m', 'Clean whitespace', '-m', 'Remove leading newlines; replace lines containing only whitespace with empty lines; replace multiple trailing newlines with a single newline'])
                done = False
                while not done:
                    time.sleep(5)
                    print('Trying to push...')
                    done = subprocess.call(['hub', 'push', 'origin'], stderr=devnull) == 0
                if raw_input('Do the thing [y/n]: ') == 'y':
                    output = subprocess.check_output(['hub', 'pull-request', '-f', '-b', owner + ':master', '-m', 'Clean whitespace\n\nRemove leading newlines; replace lines containing only whitespace with empty lines; replace multiple trailing newlines with a single newline.\n\nThis PR was created semiautomatically.'])
                    resultrows.append([row[0], 'pr', output[output.rfind('/') + 1:-1]])
                else:
                    resultrows.append(row)
            else:
                resultrows.append([row[0], 'hash', currenthash])
        os.chdir('..')
        shutil.rmtree(repo)

csvfile.close()

with open('status.csv', 'wb') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerows(resultrows)
