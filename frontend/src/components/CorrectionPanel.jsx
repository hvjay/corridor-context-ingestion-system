import {useRef, useState} from 'react';
import {Alert, Box, Button, Divider, Paper, Stack, TextField, Typography} from '@mui/material';
import AutoFixHighOutlined from '@mui/icons-material/AutoFixHighOutlined';

import {post} from '../api.js';

export default function CorrectionPanel({review, customerKey, csrfToken, onResolved}) {
  const [instruction, setInstruction] = useState('');
  const [reviewer, setReviewer] = useState('Local reviewer');
  const [proposal, setProposal] = useState(null);
  const [proposalStale, setProposalStale] = useState(false);
  const [suggestion, setSuggestion] = useState(review.suggestion || null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const requestIds = useRef({});
  const base = `/api/customers/${encodeURIComponent(customerKey)}/reviews/${review.id}`;
  function edit(setter, value) {setter(value); if (proposal) setProposalStale(true); setError(''); requestIds.current = {};}
  async function submit(action) {
    setBusy(action); setError('');
    try {
      if (action === 'accept-suggestion') {
        requestIds.current.accept ||= crypto.randomUUID();
        const result = await post(base + '/accept-suggestion', {suggestionId: suggestion.id, reviewer, requestId: requestIds.current.accept}, csrfToken);
        if (result.resolution) onResolved(result.resolution);
        else {setProposalStale(false); setProposal(result.proposal); setInstruction(suggestion.suggested_instruction); setError('The agent needs a different resolution. Review its proposal below before applying.');}
      } else if (action === 'suggestion') {
        requestIds.current.accept = null;
        setSuggestion(await post(base + '/suggestion', {}, csrfToken));
      } else if (action === 'proposals') {
        requestIds.current.proposals ||= crypto.randomUUID();
        const result = await post(base + '/proposals', {instruction, reviewer, requestId: requestIds.current.proposals}, csrfToken);
        setProposal(result); setProposalStale(false);
      } else if (action === 'apply') {
        if (proposalStale) throw new Error('Update the preview before applying your edited instructions.');
        const result = await post(base + '/apply', {proposalId: proposal.id}, csrfToken);
        onResolved(result);
      } else {
        requestIds.current.ignore ||= crypto.randomUUID();
        const result = await post(base + '/ignore', {instruction: instruction || 'Ignored without changing facts.', reviewer, requestId: requestIds.current.ignore}, csrfToken);
        onResolved(result);
      }
    } catch (err) {setError(err.message); if (err.status === 409) {setProposalStale(true); setSuggestion(value => value ? {...value, stale: true} : value); requestIds.current = {};}}
    finally {setBusy('');}
  }
  if (review.resolution) return <Alert severity={review.status === 'ignored' ? 'info' : 'success'} sx={{m: 3}}>
    <Typography variant="body2" sx={{fontWeight: 600}}>{review.resolution.summary}</Typography>
    <Typography variant="body2">Instructions: {review.resolution.instruction}</Typography>
    <Typography variant="caption">{review.resolution.reviewer} · {review.resolution.created_at}</Typography>
  </Alert>;
  return <Box sx={{p: 3, bgcolor: '#fafcf9'}}>
    <Paper variant="outlined" sx={{p: 2.5, mb: 3}}>
      <Typography variant="subtitle2">Agent recommendation</Typography>
      <Typography variant="body2" color="text.secondary" sx={{mt: 1}}>Ask the agent to assess the evidence and suggest how to resolve this case.</Typography>
      {suggestion && <Stack spacing={1.5} sx={{mt: 2}}>
        <Typography variant="body2" sx={{fontWeight: 600}}>Suggested action: {{update: 'Update facts', ignore: 'Ignore item', clarify: 'Ask for clarification'}[suggestion.action]}</Typography>
        <Typography variant="body2">{suggestion.rationale}</Typography>
        {suggestion.evidence.map((quote, i) => <Typography key={i} variant="body2" component="blockquote" sx={{m: 0, pl: 2, borderLeft: '3px solid', borderColor: 'divider'}}>“{quote}”</Typography>)}
        {suggestion.question && <Alert severity="info">{suggestion.question} Add the confirmed answer in Correction instructions below.</Alert>}
        {suggestion.suggested_instruction && <Typography variant="body2">Suggested instructions: {suggestion.suggested_instruction}</Typography>}
        <Typography variant="caption" color="text.secondary">{suggestion.model} · {suggestion.created_at} · Nothing applied</Typography>
        {suggestion.stale && <Alert severity="warning">Facts or source context changed. Generate a fresh recommendation before using it.</Alert>}
        {suggestion.suggested_instruction && suggestion.action !== 'clarify' && <>
          <Typography variant="caption" color="text.secondary">Accepting applies these instructions and resolves the item. Fact history is retained.</Typography>
          <Button variant="contained" disabled={!!busy || suggestion.stale || !suggestion.id || !reviewer.trim()} onClick={() => submit('accept-suggestion')}>
            {busy === 'accept-suggestion' ? 'Accepting…' : 'Accept AI suggestion'}
          </Button>
        </>}
        {suggestion.suggested_instruction && <Button variant="outlined" disabled={!!busy || suggestion.stale} onClick={() => edit(setInstruction, suggestion.suggested_instruction)}>Use suggested instructions</Button>}
      </Stack>}
      {(!suggestion || suggestion.stale) && <Button sx={{mt: 2}} variant="outlined" startIcon={<AutoFixHighOutlined/>} disabled={!!busy} onClick={() => submit('suggestion')}>
        {busy === 'suggestion' ? 'Assessing evidence…' : suggestion ? 'Refresh recommendation' : 'Suggest resolution'}
      </Button>}
    </Paper>
    <Typography variant="subtitle2" sx={{mb: 0.5}}>Resolve this item</Typography>
    <Typography variant="body2" color="text.secondary" sx={{mb: 2}}>Tell the agent what to correct. You’ll review its proposed changes before applying them.</Typography>
    <Stack spacing={2}>
      <TextField multiline minRows={3} label="Correction instructions" placeholder="For this customer, set the employee count to 25. The 200 employees refer to the previous employer."
        value={instruction} onChange={event => edit(setInstruction, event.target.value)} disabled={!!busy}
        slotProps={{htmlInput: {maxLength: 6000}}}/>
      <TextField size="small" label="Your name" value={reviewer} onChange={event => edit(setReviewer, event.target.value)} disabled={!!busy}
        slotProps={{htmlInput: {maxLength: 120}}} sx={{maxWidth: 320}}/>
      {error && <Alert severity="error">{error}</Alert>}
      {proposal && <Paper variant="outlined" sx={{p: 2.5}}>
        <Typography variant="overline" color="text.secondary">Agent proposal · {proposalStale ? 'Out of date' : 'Not yet applied'}</Typography>
        {proposalStale && <Alert severity="warning" sx={{mt: 1}}>This preview is out of date. Click Update preview to include your latest instructions and reviewer details before applying.</Alert>}
        {proposal.instruction && <Typography variant="body2" color="text.secondary" sx={{mt: 1}}>Instructions used: {proposal.instruction}</Typography>}
        <Typography variant="body2" sx={{mt: 1, mb: 2}}>{proposal.summary}</Typography>
        {proposal.action === 'clarify' ? <Alert severity="info">{proposal.question} Update your instructions and preview again.</Alert> : <>
          <Stack spacing={2} divider={<Divider/>}>{proposal.changes.map(change => <Box key={change.fact_type}>
            <Typography variant="subtitle2">{change.label}</Typography>
            <Typography variant="body2" color="text.secondary">Current: {change.before?.display_value || 'Not recorded'}</Typography>
            <Typography variant="body2" sx={{fontWeight: 600}}>Proposed: {change.operation === 'remove' ? 'Remove current fact (history retained)' : change.display_value}</Typography>
          </Box>)}</Stack>
          {proposal.action === 'ignore' && <Alert severity="info">This will resolve the item without changing customer facts.</Alert>}
          <Button variant="contained" onClick={() => submit('apply')} disabled={!!busy || proposalStale} sx={{mt: 2}}>
            {busy === 'apply' ? 'Applying…' : proposal.action === 'ignore' ? 'Apply: ignore item' : 'Apply correction'}
          </Button>
        </>}
      </Paper>}
      <Stack direction="row" spacing={1.5} sx={{flexWrap: 'wrap', rowGap: 1}}>
        <Button variant="outlined" startIcon={<AutoFixHighOutlined/>} onClick={() => submit('proposals')} disabled={!!busy || !instruction.trim() || !reviewer.trim()}>
          {busy === 'proposals' ? 'Preparing proposal…' : proposalStale && proposal ? 'Update preview' : 'Preview correction'}
        </Button>
        <Button color="inherit" onClick={() => submit('ignore')} disabled={!!busy || !reviewer.trim()}>
          {busy === 'ignore' ? 'Ignoring…' : 'Ignore without changes'}
        </Button>
      </Stack>
    </Stack>
  </Box>;
}
