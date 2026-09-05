const REPOSITORY_OWNER = 'megumiss';
const REPOSITORY_NAME = 'NIKKEAutoScript';
const LICENSE_TTL_SECONDS = 365 * 24 * 60 * 60;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function base64Url(value) {
  const bytes = value instanceof ArrayBuffer ? new Uint8Array(value) : new TextEncoder().encode(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function decodeBase64(value) {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function pemToBytes(pem) {
  return decodeBase64(pem.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g, ''));
}

function callbackUri(env) {
  return env.APP_CALLBACK_URI || 'nkas://auth/callback';
}

function redirectToApp(env, values) {
  const callback = new URL(callbackUri(env));
  Object.entries(values).forEach(([key, value]) => callback.searchParams.set(key, value));
  return Response.redirect(callback.toString(), 302);
}

async function githubRequest(path, token) {
  return fetch(`https://api.github.com${path}`, {
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'User-Agent': 'NKAS-Mobile',
    },
  });
}

async function signLicense(username, env) {
  if (!env.LICENSE_PRIVATE_KEY_PEM) throw new Error('license_key_not_configured');
  const header = base64Url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
  const now = Math.floor(Date.now() / 1000);
  const payload = base64Url(JSON.stringify({
    iss: 'nkas-license-worker',
    sub: username,
    repo: `${REPOSITORY_OWNER}/${REPOSITORY_NAME}`,
    starred: true,
    iat: now,
    exp: now + LICENSE_TTL_SECONDS,
  }));
  const input = `${header}.${payload}`;
  const key = await crypto.subtle.importKey(
    'pkcs8',
    pemToBytes(env.LICENSE_PRIVATE_KEY_PEM),
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(input));
  return `${input}.${base64Url(signature)}`;
}

async function handleOAuthCallback(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state') || '';
  if (url.searchParams.get('error')) {
    return redirectToApp(env, { state, error: 'oauth_cancelled' });
  }

  const code = url.searchParams.get('code');
  if (!code || !env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET || !env.GITHUB_OAUTH_CALLBACK_URL) {
    return redirectToApp(env, { state, error: 'oauth_not_configured' });
  }

  try {
    const tokenResponse = await fetch('https://github.com/login/oauth/access_token', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'NKAS-Mobile',
      },
      body: JSON.stringify({
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        code,
        redirect_uri: env.GITHUB_OAUTH_CALLBACK_URL,
      }),
    });
    const tokenData = await tokenResponse.json();
    if (!tokenResponse.ok || !tokenData.access_token) throw new Error('github_token_exchange_failed');

    const userResponse = await githubRequest('/user', tokenData.access_token);
    const user = await userResponse.json();
    if (!userResponse.ok || !user.login) throw new Error('github_user_lookup_failed');

    const starredResponse = await githubRequest(
      `/user/starred/${REPOSITORY_OWNER}/${REPOSITORY_NAME}`,
      tokenData.access_token,
    );
    if (starredResponse.status !== 204) {
      return redirectToApp(env, {
        state,
        error: starredResponse.status === 404 ? 'repository_not_starred' : 'star_check_failed',
      });
    }

    return redirectToApp(env, { state, key: await signLicense(user.login, env) });
  } catch (error) {
    return redirectToApp(env, { state, error: error instanceof Error ? error.message : 'oauth_failed' });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/' && request.method === 'GET') {
      return json({ service: 'nkas-license', repository: `${REPOSITORY_OWNER}/${REPOSITORY_NAME}` });
    }

    if (url.pathname === '/oauth/start' && request.method === 'GET') {
      if (!env.GITHUB_CLIENT_ID || !env.GITHUB_OAUTH_CALLBACK_URL) {
        return json({ error: 'oauth_not_configured' }, 503);
      }
      const state = url.searchParams.get('state');
      if (!state || !/^[0-9a-f-]{16,80}$/i.test(state)) {
        return json({ error: 'invalid_state' }, 400);
      }
      const authorize = new URL('https://github.com/login/oauth/authorize');
      authorize.searchParams.set('client_id', env.GITHUB_CLIENT_ID);
      authorize.searchParams.set('redirect_uri', env.GITHUB_OAUTH_CALLBACK_URL);
      authorize.searchParams.set('scope', 'user');
      authorize.searchParams.set('state', state);
      return Response.redirect(authorize.toString(), 302);
    }

    if (url.pathname === '/oauth/callback' && request.method === 'GET') {
      return handleOAuthCallback(request, env);
    }

    return json({ error: 'not_found' }, 404);
  },
};
