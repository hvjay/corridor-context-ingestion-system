import {useEffect, useState} from 'react';

async function readJson(path, signal) {
  const response = await fetch(path, {cache: 'no-store', signal});
  if (!response.ok) throw new Error(response.status === 404
    ? 'Customer not found.' : 'Could not load review data. Check the server and try Refresh.');
  return response.json();
}
function selectedFromUrl() {
  try {return decodeURIComponent(window.location.hash.slice(1));}
  catch {return '__invalid__';}
}
export function useReviews() {
  const [customers, setCustomers] = useState([]);
  const [selected, setSelected] = useState(selectedFromUrl);
  const [data, setData] = useState(null);
  const [demo, setDemo] = useState(false);
  const [csrfToken, setCsrfToken] = useState('');
  const [loading, setLoading] = useState(true);
  const [listLoading, setListLoading] = useState(true);
  const [error, setError] = useState('');
  const [listError, setListError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const onHashChange = () => setSelected(selectedFromUrl());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setListError(''); setListLoading(true);
    readJson('/api/customers', controller.signal).then(result => {
      setCustomers(result.customers); setDemo(result.demo); setCsrfToken(result.csrfToken); setListLoading(false);
      setSelected(previous => previous || result.customers[0]?.key || '');
      if (!result.customers.length) setLoading(false);
    }).catch(err => {
      if (err.name !== 'AbortError') {setListError(err.message); setListLoading(false); setLoading(false);}
    });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setData(null);
    readJson('/api/customers/' + encodeURIComponent(selected), controller.signal).then(result => {
      setData(result); setLoading(false);
    }).catch(err => {
      if (err.name !== 'AbortError') {setError(err.message); setLoading(false);}
    });
    return () => controller.abort();
  }, [selected, revision]);
  return {customers, selected, data, demo, loading, listLoading, csrfToken,
    error: error || listError, refresh: () => setRevision(n => n + 1)};
}
