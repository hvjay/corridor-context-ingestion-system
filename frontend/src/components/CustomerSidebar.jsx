import {useState} from 'react';
import {Box, Chip, InputAdornment, List, ListItemButton, ListItemText, Stack, TextField, Typography} from '@mui/material';
import SearchRounded from '@mui/icons-material/SearchRounded';

export default function CustomerSidebar({customers, selected}) {
  const [query, setQuery] = useState('');
  const visible = customers.filter(customer => customer.name.toLowerCase().includes(query.toLowerCase()));
  return <Box component="aside" sx={{width: {xs: '100%', md: 272}, flexShrink: 0, bgcolor: 'background.paper',
    borderRight: {md: '1px solid #e2e9e3'}, borderBottom: {xs: '1px solid #e2e9e3', md: 0}, p: 2.5,
    display: 'flex', flexDirection: 'column'}}>
    <Stack direction="row" sx={{mb: 2, px: 1, justifyContent: "space-between", alignItems: "center"}}>
      <Typography variant="subtitle2" sx={{fontWeight: 650}}>Customers</Typography><Chip size="small" label={customers.length} variant="outlined"/>
    </Stack>
    <TextField size="small" type="search" placeholder="Search customers" value={query}
      onChange={event => setQuery(event.target.value)}
      slotProps={{htmlInput: {'aria-label': 'Search customers'}, input: {startAdornment: <InputAdornment position="start"><SearchRounded fontSize="small"/></InputAdornment>}}}/>
    <List component="nav" aria-label="Customers" sx={{mt: 1.5, display: {xs: 'grid', md: 'block'}, gridTemplateColumns: {sm: 'repeat(3, 1fr)'}}}>
      {visible.map(customer => <ListItemButton key={customer.key} component="a" href={'#' + encodeURIComponent(customer.key)}
        selected={selected === customer.key} aria-current={selected === customer.key ? 'page' : undefined} sx={{mb: 0.5, px: 1.5, py: 1.3, gap: 1}}>
        <ListItemText primary={customer.name} slotProps={{primary: {fontSize: 13, fontWeight: selected === customer.key ? 650 : 500}}}/>
        <Chip size="small" label={customer.review_count} sx={{height: 22, bgcolor: selected === customer.key ? '#dbe8d9' : '#f0f3ef'}}/>
      </ListItemButton>)}
      {!visible.length && query && <Typography variant="body2" color="text.secondary" sx={{p: 1}}>No matching customers.</Typography>}
    </List>
    <Box sx={{display: {xs: 'none', md: 'block'}, mt: 'auto', pt: 3}}>
      <Typography variant="caption" color="text.secondary">Refresh to see newly ingested passages.</Typography>
    </Box>
  </Box>;
}
