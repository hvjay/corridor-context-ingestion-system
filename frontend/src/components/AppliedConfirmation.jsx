import {Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Divider, Stack, Typography} from '@mui/material';

export function resolutionChanges(resolution) {
  const before = JSON.parse(resolution.before_json || '{}');
  const after = JSON.parse(resolution.after_json || '{}');
  const previous = before.facts || {}, current = after.facts || {};
  return [...new Set([...Object.keys(previous), ...Object.keys(current)])].flatMap(key => {
    const old = previous[key], next = current[key];
    if (JSON.stringify(old?.value) === JSON.stringify(next?.value)) return [];
    return [{key, label: after.definitions?.[key]?.label || before.definitions?.[key]?.label || key,
      before: old ? old.display_value || JSON.stringify(old.value) : 'Not recorded',
      after: next ? next.display_value || JSON.stringify(next.value) : 'Removed (history retained)'}];
  });
}

export default function AppliedConfirmation({receipt, onClose, onViewResolved}) {
  if (!receipt) return null;
  const {resolution, customer} = receipt;
  const changes = resolutionChanges(resolution);
  return <Dialog open onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="applied-title">
    <DialogTitle id="applied-title">{resolution.action === 'ignored' ? 'Review ignored' : 'Proposal applied'}</DialogTitle>
    <DialogContent>
      <Alert severity="success" sx={{mb: 2}}>Saved to the database for {customer}.</Alert>
      <Typography variant="body2" sx={{mb: 2}}>{resolution.summary}</Typography>
      <Stack spacing={2} divider={<Divider/>}>{changes.map(change => <div key={change.key}>
        <Typography variant="subtitle2">{change.label}</Typography>
        <Typography variant="body2" color="text.secondary">Before: {change.before}</Typography>
        <Typography variant="body2" sx={{fontWeight: 600}}>After: {change.after}</Typography>
      </div>)}</Stack>
      {!changes.length && <Typography variant="body2">{resolution.action === 'ignored' ? 'No customer facts were changed.' : 'The accepted values were already current; no fact values changed.'}</Typography>}
      <Typography variant="caption" color="text.secondary" sx={{display: 'block', mt: 2}}>Recorded by {resolution.reviewer} · {resolution.created_at}</Typography>
    </DialogContent>
    <DialogActions>
      <Button onClick={onViewResolved}>View resolved items</Button>
      <Button variant="contained" onClick={onClose}>Done</Button>
    </DialogActions>
  </Dialog>;
}
