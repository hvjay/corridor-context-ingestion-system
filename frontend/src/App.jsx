import {useState} from 'react';
import {ToggleButton, ToggleButtonGroup, Alert, AppBar, Avatar, Box, Button, Chip, Container, Divider, Paper, Skeleton, Stack, Toolbar, Typography} from '@mui/material';
import RefreshRounded from '@mui/icons-material/RefreshRounded';
import FactCheckOutlined from '@mui/icons-material/FactCheckOutlined';
import CustomerSidebar from './components/CustomerSidebar.jsx';
import ReviewCard from './components/ReviewCard.jsx';
import AppliedConfirmation from './components/AppliedConfirmation.jsx';
import {useReviews} from './hooks/useReviews.js';
export {default as ReviewCard} from './components/ReviewCard.jsx';

function Summary({reviews, resolved}) {
  return <Paper variant="outlined" sx={{p: 3, my: 3.5, bgcolor: '#eef4ed', borderColor: '#dce7d9'}}>
    <Stack direction={{xs: 'column', sm: 'row'}} spacing={3} sx={{alignItems: {sm: "center"}}}>
      <Stack direction="row" spacing={5}>
        <Box><Typography variant="overline" color="text.secondary">{resolved ? 'Resolved passages' : 'Passages to review'}</Typography><Typography variant="h4">{reviews.length}</Typography></Box>
        <Box><Typography variant="overline" color="text.secondary">Proposed facts</Typography><Typography variant="h4">{reviews.reduce((count, review) => count + review.candidates.length, 0)}</Typography></Box>
      </Stack>
      <Divider flexItem orientation="vertical" sx={{display: {xs: 'none', sm: 'block'}}}/>
      <Box><Typography variant="body2" sx={{fontWeight: 550}}>Employer attribution is unconfirmed.</Typography><Typography variant="caption" color="text.secondary">Candidates are separate from accepted customer facts.</Typography></Box>
    </Stack>
  </Paper>;
}
export default function App() {
  const {customers, selected, data, demo, loading, listLoading, error, refresh, csrfToken} = useReviews();
  const [filter, setFilter] = useState('open');
  const [receipt, setReceipt] = useState(null);
  const visibleReviews = data?.reviews.filter(review => filter === 'open' ? !review.resolution : !!review.resolution) || [];
  const name = data?.customer.name || customers.find(customer => customer.key === selected)?.name || 'Review ambiguous facts';
  function resolved(resolution) {setReceipt({resolution, customer: name}); refresh();}
  return <>
    <AppliedConfirmation receipt={receipt} onClose={() => setReceipt(null)} onViewResolved={() => {setFilter('resolved'); setReceipt(null);}}/>
    <AppBar position="static" color="inherit" elevation={0} sx={{borderBottom: 1, borderColor: 'divider'}}>
      <Toolbar sx={{minHeight: '76px !important', gap: 2, px: {xs: 2.5, md: 4}}}>
        <Avatar variant="rounded" sx={{bgcolor: 'primary.main', width: 34, height: 34, font: 'bold 32px Georgia'}}>c</Avatar>
        <Typography sx={{fontSize: 24, fontWeight: 700, letterSpacing: -0.8}}>corridor</Typography>
        <Divider orientation="vertical" flexItem sx={{my: 3, mx: 0.5}}/>
        <Typography variant="body2" sx={{fontWeight: 500}}>Context review</Typography>
        <Chip size="small" label="Local workspace" variant="outlined" sx={{ml: 'auto', display: {xs: 'none', sm: 'flex'}}}/>
      </Toolbar>
    </AppBar>
    {demo && <Alert severity="info" icon={false} sx={{borderRadius: 0, py: 0, px: {xs: 2.5, md: 4}, bgcolor: '#e8f0df', color: '#516843'}}>
      Demo workspace · Sample transcripts, analyzed with offline rules, in a separate database.
    </Alert>}
    <Box sx={{display: 'flex', flexDirection: {xs: 'column', md: 'row'}, minHeight: 'calc(100vh - 76px)'}}>
      <CustomerSidebar customers={customers} selected={selected}/>
      <Box component="main" sx={{flex: 1, minWidth: 0}}>
        <Container maxWidth="lg" sx={{py: {xs: 3, md: 5}, px: {xs: 2.5, md: 5}}}>
          <Typography variant="overline" color="text.secondary">Customer context / Human review</Typography>
          <Stack direction="row" sx={{mt: 2, mb: 2, justifyContent: "space-between", alignItems: "center", gap: 2}}>
            <Box><Typography component="h1" variant="h4" sx={{fontSize: {xs: 26, sm: 32}}}>{name}</Typography>
              <Typography variant="body2" color="text.secondary" sx={{mt: 1}}>Inspect proposed facts and the evidence behind them.</Typography></Box>
            <Button variant="outlined" onClick={refresh} startIcon={<RefreshRounded/>} sx={{flexShrink: 0, bgcolor: 'background.paper'}}>Refresh</Button>
          </Stack>
          {error && <Alert severity="error" sx={{my: 2}}>{error}</Alert>}
          {data && <Summary reviews={visibleReviews} resolved={filter === "resolved"}/>}
          <Stack direction="row" spacing={1.5} sx={{mt: 3.5, mb: 2, alignItems: "center"}}>
            <Typography component="h2" variant="h6">{filter === 'open' ? 'Needs review' : 'Resolved items'}</Typography>
            <ToggleButtonGroup size="small" exclusive value={filter} onChange={(_, value) => value && setFilter(value)} aria-label="Review status">
              <ToggleButton value="open">Open</ToggleButton><ToggleButton value="resolved">Resolved</ToggleButton>
            </ToggleButtonGroup>
          </Stack>
          <Stack spacing={2.5} aria-live="polite" aria-busy={loading || listLoading}>
            {loading ? <><Typography variant="body2" color="text.secondary">Loading review items…</Typography>{[0, 1].map(key => <Skeleton key={key} variant="rounded" height={190}/>)}</> :
              visibleReviews.length ? visibleReviews.map(review => <ReviewCard key={`${selected}-${review.id}`} review={review} customerKey={selected} csrfToken={csrfToken} onResolved={resolved}/>) : !error &&
              <Paper variant="outlined" sx={{p: 6, textAlign: 'center', borderStyle: 'dashed'}}><FactCheckOutlined sx={{fontSize: 38, color: 'primary.main', mb: 1}}/>
                <Typography variant="h6">{filter === 'resolved' ? 'No resolved items yet.' : customers.length ? 'No ambiguous passages for this customer yet.' : 'No customers configured.'}</Typography>
                <Typography variant="body2" color="text.secondary" sx={{mt: 1}}>Newly flagged passages will appear here after ingestion.</Typography>
              </Paper>}
          </Stack>
          <Typography component="footer" variant="caption" color="text.secondary" sx={{display: 'block', mt: 3}}>Review the full source before deciding which employer a statement belongs to.</Typography>
        </Container>
      </Box>
    </Box>
  </>;
}
