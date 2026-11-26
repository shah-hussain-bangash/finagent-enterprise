import test from 'node:test';
import assert from 'node:assert/strict';
import { NextRequest } from 'next/server.js';
import { finishPasswordLogin, passwordLoginEnabled, passwordLoginAvailable, readPasswordForm, revokeStoredSession } from './password-auth.js';
import { POST } from './password-route.js';
import { authStore } from './auth-store.js';

Object.assign(process.env, {
  FINAGENT_ENVIRONMENT: 'test', FINAGENT_WEB_AUTH_MODE: 'google',
  FINAGENT_WEB_PASSWORD_LOGIN_ENABLED: 'true',
  FINAGENT_GOOGLE_WEB_CLIENT_ID: 'synthetic.apps.googleusercontent.com',
  FINAGENT_GOOGLE_WEB_CLIENT_SECRET: 'synthetic-client-secret',
  FINAGENT_WEB_ORIGIN: 'http://127.0.0.1:3001',
  FINAGENT_API_URL: 'http://127.0.0.1:8001', FINAGENT_WEB_AUTH_REDIS_URL: 'redis://127.0.0.1:1/0',
});
const email = 'analyst@example.com', password = 'Synthetic testing password 901!';
const token = 'fa_pwd_' + 'A'.repeat(43);
const form = (body) => new Request('http://127.0.0.1:3001/auth/password', {
  method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body,
});

for (const value of [true, false]) {
  test('password capability uses its own endpoint: ' + value, async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async (url) => {
      assert(url.endsWith('/api/v1/auth/password/config'));
      return Response.json({enabled:value});
    };
    try { assert.equal(await passwordLoginAvailable(),value); }
    finally { globalThis.fetch = original; }
  });
}
test('password discovery failure is contained', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('Unavailable'); };
  try { assert.equal(await passwordLoginAvailable(),false); }
  finally { globalThis.fetch = original; }
});
test('disabled web password feature does not call discovery', async () => {
  const original = globalThis.fetch, enabled = process.env.FINAGENT_WEB_PASSWORD_LOGIN_ENABLED;
  process.env.FINAGENT_WEB_PASSWORD_LOGIN_ENABLED='false';
  globalThis.fetch = () => { throw new Error('Should not call'); };
  try { assert.equal(await passwordLoginAvailable(),false); }
  finally { globalThis.fetch = original; process.env.FINAGENT_WEB_PASSWORD_LOGIN_ENABLED=enabled; }
});

test('password sign-in is opt-in and cannot use the synthetic local issuer mode', () => {
  assert.equal(passwordLoginEnabled({FINAGENT_WEB_AUTH_MODE:'google'}), false);
  assert.equal(passwordLoginEnabled({FINAGENT_WEB_AUTH_MODE:'local', FINAGENT_ENVIRONMENT:'test',
                                    FINAGENT_WEB_PASSWORD_LOGIN_ENABLED:'true'}), false);
});

test('form decoding preserves password spaces and equals signs', async () => {
  assert.deepEqual(await readPasswordForm(form('email=a%40example.com&password=%20a%3Db%20')),
                   {email:'a@example.com', password:' a=b '});
});
test('password form carries one optional case destination and rejects duplicates', async () => {
  assert.deepEqual(await readPasswordForm(form('email=a&password=b&returnTo=%2Fcases%2Fabc')),
                   {email:'a', password:'b', returnTo:'/cases/abc'});
  await assert.rejects(() => readPasswordForm(form('email=a&password=b&returnTo=a&returnTo=b')));
});
for (const body of ['email=a&password=b&role=ANALYST', 'email=a&email=b&password=c',
                    'email=a', 'email=a&password=' + 'x'.repeat(4096)]) {
  test('invalid or oversized login form is rejected: ' + body.slice(0, 35), async () => {
    await assert.rejects(() => readPasswordForm(form(body)));
  });
}

test('login rejects cross-origin requests before any API call', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('Should not reach API'); };
  try {
    const response = await POST(new NextRequest('http://127.0.0.1:3001/auth/password', {
      method: 'POST', headers: {origin:'https://evil.example', 'Content-Type':'application/x-www-form-urlencoded'},
      body: new URLSearchParams({email,password}),
    }));
    assert.equal(response.status, 400);
    assert.equal(response.headers.get('cache-control'), 'no-store');
  } finally { globalThis.fetch = original; }
});

function store() {
  const records = new Map();
  return {records,
    async put(kind, id, value) { records.set(id, value); },
    async read(kind, id) { return records.get(id) ?? null; },
    async remove(kind, id) { records.delete(id); },
  };
}
function loginResponse(overrides={}) {
  return {access_token:token, token_type:'Bearer', expires_at:Math.floor(Date.now()/1000)+1700,
          subject:'synthetic-analyst', mfa_satisfied:false, ...overrides};
}
test('password route restores only a validated case destination', async () => {
  const original = globalThis.fetch, methods = { ...authStore }, memory = store();
  Object.assign(authStore, memory);
  globalThis.fetch = async (url) => Response.json(url.endsWith('/login') ? loginResponse() : {roles:['ANALYST']});
  try {
    const path = '/cases/bf69eaa7-86f5-428f-8e1d-bfa2c57420fd/report';
    for (const [returnTo, expected] of [[path, path], ['//evil.example', '/']]) {
      const response = await POST(new NextRequest('http://127.0.0.1:3001/auth/password', {
        method:'POST', headers:{origin:'http://127.0.0.1:3001', 'Content-Type':'application/x-www-form-urlencoded'},
        body:new URLSearchParams({email, password, returnTo}),
      }));
      assert.equal(response.headers.get('location'), `http://127.0.0.1:3001${expected}`);
    }
  } finally { globalThis.fetch = original; Object.assign(authStore, methods); }
});
test('successful login returns only an opaque handle and keeps credentials server-side', async () => {
  const original = globalThis.fetch, memory = store();
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push([url,init]);
    return Response.json(url.endsWith('/login') ? loginResponse() : {roles:['ANALYST']});
  };
  try {
    const result = await finishPasswordLogin(email,password,undefined,memory);
    assert.match(result.handle,/^[a-f0-9]{64}$/);
    assert.deepEqual(Object.keys(result).sort(),['exp','handle']);
    assert.equal(memory.records.get(result.handle).token,token);
    assert.equal(calls.length,2);
    assert.equal(JSON.parse(calls[0][1].body).password,password);
    assert.equal(calls[0][1].redirect,'error');
    assert.equal(calls[1][1].headers.Authorization,'Bearer '+token);
  } finally { globalThis.fetch = original; }
});

test('failed credentials leave no session', async () => {
  const original = globalThis.fetch, memory = store();
  globalThis.fetch = async () => new Response('',{status:401});
  try {
    await assert.rejects(() => finishPasswordLogin(email,password,undefined,memory));
    assert.equal(memory.records.size,0);
  } finally { globalThis.fetch = original; }
});

test('a claimed MFA response is rejected', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json(loginResponse({mfa_satisfied:true}));
  try { await assert.rejects(() => finishPasswordLogin(email,password,undefined,store())); }
  finally { globalThis.fetch = original; }
});

test('identity denial revokes newly issued token and leaves no application session', async () => {
  const original = globalThis.fetch, memory = store(), calls=[];
  globalThis.fetch = async (url) => {
    calls.push(url);
    if (url.endsWith('/login')) return Response.json(loginResponse());
    return new Response(null,{status:url.endsWith('/logout')?204:401});
  };
  try {
    await assert.rejects(() => finishPasswordLogin(email,password,undefined,memory));
    assert.equal(memory.records.size,0);
    assert(calls.some(url=>url.endsWith('/logout')));
  } finally { globalThis.fetch = original; }
});

test('logout revokes backend password token before deleting stored session', async () => {
  const original = globalThis.fetch, memory = store(), handle='b'.repeat(64);
  memory.records.set(handle,{token});
  let revoked=false;
  globalThis.fetch = async (url, init) => {
    assert(url.endsWith('/logout'));
    assert(memory.records.has(handle));
    assert.equal(init.headers.Authorization,'Bearer '+token);
    revoked=true;
    return new Response(null,{status:204});
  };
  try {
    await revokeStoredSession(handle,memory);
    assert(revoked && !memory.records.has(handle));
  } finally { globalThis.fetch = original; }
});

test('failed backend revocation preserves session for a retry', async () => {
  const original = globalThis.fetch, memory = store(), handle='c'.repeat(64);
  memory.records.set(handle,{token});
  globalThis.fetch = async () => new Response(null,{status:503});
  try {
    await assert.rejects(() => revokeStoredSession(handle,memory));
    assert(memory.records.has(handle));
  } finally { globalThis.fetch = original; }
});
