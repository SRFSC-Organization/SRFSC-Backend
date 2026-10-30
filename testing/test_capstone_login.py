"""Exercise capstone2pages' real account client against Flask through a local test transport."""
import os
from pathlib import Path
import subprocess
import json
import unittest

from flask_cors import CORS

from __init__ import allowed_origins
from testing import test_srfsc_profile as fixtures


class CapstoneLoginTest(unittest.TestCase):
    def test_frontend_login_profile_and_logout(self):
        frontend = Path(os.environ.get('CAPSTONE_FRONTEND', '../capstone2pages')).resolve()
        source = frontend / '_projects/systems/srfsc/js/memberApi.js'
        self.assertTrue(source.is_file(), f'Frontend client not found: {source}')
        fixture = fixtures.ProfileTest()
        fixture.setUp()
        try:
            CORS(fixture.app, supports_credentials=True, origins=allowed_origins)
            # A line-oriented transport lets the actual JS client call Flask
            # without needing a network listener. Flask manages its cookie jar.
            process = subprocess.Popen(
                ['node', '--input-type=module', '-e', NODE_FLOW, str(source)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            try:
                for line in process.stdout:
                    request = json.loads(line)
                    response = fixture.client.open(
                        request['path'], method=request['method'],
                        headers=request['headers'], data=request.get('body'),
                    )
                    process.stdin.write(json.dumps({
                        'status': response.status_code,
                        'body': response.get_data(as_text=True),
                        'headers': dict(response.headers),
                    }) + '\n')
                    process.stdin.flush()
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr.read())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
        finally:
            fixture.tearDown()


NODE_FLOW = r'''
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
const origin = 'https://srfsc-organization.github.io';
const endpoint = 'http://localhost:8587';
const replies = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
globalThis.fetch = async (url, options = {}) => {
  console.log(JSON.stringify({ path: new URL(url).pathname, method: options.method || 'GET',
    headers: { ...options.headers, Origin: origin }, body: options.body }));
  const { value } = await replies.next();
  const data = JSON.parse(value);
  const response = new Response(data.body, { status: data.status, headers: data.headers });
  assert.equal(response.headers.get('access-control-allow-origin'), origin);
  assert.equal(response.headers.get('access-control-allow-credentials'), 'true');
  return response;
};
const source = (await readFile(process.argv[1], 'utf8')).replace(
  "import { srfscAccountURI, fetchOptions } from '../../api/config.js';",
  `const srfscAccountURI = ${JSON.stringify(endpoint)}; const fetchOptions = {
    credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Origin': 'client' }
  };`,
);
const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
assert.equal((await api.checkLoginService()).accounts_available, true);
await assert.rejects(api.loginMember({ uid: 'neighbor', password: 'wrong' }), e => e.status === 401);
assert.equal((await api.loginMember({ uid: 'neighbor', password: 'TestPassword123!' })).user.uid, 'neighbor');
assert.equal((await api.loadMemberProfile()).completed, false);
const answers = { participation: 'volunteer', interests: ['home', 'events'],
  experience: 'new', availability: 'weekends', goal: 'help' };
await api.saveMemberProfile({ name: 'Integration Test Member', answers });
assert.deepEqual((await api.loadMemberProfile()).answers, answers);
await api.logoutMember();
await assert.rejects(api.loadMemberProfile(), e => e.status === 401);
await assert.rejects(api.loginMember({ uid: 'pending', password: 'TestPassword123!' }), e => e.status === 403);
process.exit(0);
'''


if __name__ == '__main__':
    unittest.main()
