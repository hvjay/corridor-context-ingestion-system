async function sessionToken() {
  const response = await fetch('/api/customers', {cache: 'no-store'});
  if (!response.ok) throw new Error('Could not start a correction session. Check the server and try again.');
  const result = await response.json();
  if (!result.csrfToken) throw new Error('This backend is an older version without correction support. Restart corridor-review, then reload this page.');
  return result.csrfToken;
}

export async function post(path, body, csrfToken) {
  let token = csrfToken || await sessionToken();
  for (let attempt = 0; attempt < 2; attempt++) {
    const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token}, body: JSON.stringify(body)});
    const result = await response.json();
    if (response.ok) return result;
    if (response.status === 403 && attempt === 0) {
      token = await sessionToken();
      continue;
    }
    const error = new Error(result.error || 'Unable to complete this request. Refresh to check its status.');
    error.status = response.status;
    throw error;
  }
}
