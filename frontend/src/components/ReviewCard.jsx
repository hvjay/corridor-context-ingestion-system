import {Accordion, AccordionDetails, AccordionSummary, Alert, Box, Card, Chip, Divider, Stack, Typography} from '@mui/material';
import CorrectionPanel from './CorrectionPanel.jsx';
import ExpandMoreRounded from '@mui/icons-material/ExpandMoreRounded';

function EvidenceLines({title, lines, flaggedIndex}) {
  return <Box sx={{mt: 3}}><Typography variant="overline" color="text.secondary">{title}</Typography>
    <Stack divider={<Divider/>}>{lines.map(line => <Box key={line.index} sx={{py: 1.5, px: 1.5, bgcolor: line.index === flaggedIndex ? "#faf6eb" : "transparent", borderRadius: 1}}>
      <Stack direction="row" spacing={1} sx={{mb: 0.5, alignItems: "center"}}>
        <Typography variant="caption" color="text.secondary">Passage {line.index + 1} · {line.speaker?.name || line.speaker?.email || 'Unknown speaker'}</Typography>
        {line.index === flaggedIndex && <Chip label="Flagged" size="small" color="warning" variant="outlined"/>}
      </Stack>
      {line.start_time && <Typography variant="caption" color="text.secondary" sx={{display: 'block', mb: 1}}>{line.start_time}{line.end_time && ` → ${line.end_time}`}</Typography>}
      <Typography variant="body2" sx={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{line.text}</Typography>
    </Box>)}</Stack>
  </Box>;
}
export default function ReviewCard({review, customerKey, csrfToken, onResolved}) {
  const snapshot = review.snapshot || {}, segment = snapshot.segment || {}, speaker = segment.speaker || {};
  const attribution = snapshot.attribution, context = snapshot.context || [];
  const transcript = review.transcript || {origin: snapshot.transcript ? 'captured' : 'unavailable', segments: snapshot.transcript || [], flagged_index: review.segment_index};
  return <Card component="article" variant="outlined" sx={{overflow: 'hidden'}}>
    <Stack direction={{xs: 'column', sm: 'row'}} sx={{px: 3, pt: 2.5, alignItems: {xs: "flex-start", sm: "center"}, justifyContent: "space-between", gap: 1}}>
      <Chip label={review.status === "updated" ? "Corrected" : review.status === "ignored" ? "Ignored" : "Attribution unconfirmed"} color={review.status === "updated" ? "success" : review.status === "ignored" ? "default" : "warning"} size="small" variant="outlined"/>
      <Typography variant="caption" color="text.secondary" sx={{overflowWrap: 'anywhere'}}>{review.meeting_id}</Typography>
    </Stack>
    <Stack direction="row" sx={{p: 3, flexWrap: "wrap", gap: 4}}>
      {review.candidates.length ? review.candidates.map((candidate, index) => <Box key={index} sx={{minWidth: 150, maxWidth: '100%'}}>
        <Typography variant="caption" color="text.secondary">{candidate.label}</Typography>
        <Typography sx={{fontSize: 23, fontWeight: 550, mt: 0.5, overflowWrap: 'anywhere'}}>{candidate.display_value}</Typography>
        {candidate.subject_client_key && <Typography variant="caption" color="text.secondary">Proposed subject: {candidate.subject_client_key}</Typography>}
      </Box>) : <Box><Typography variant="caption" color="text.secondary">Unstructured passage</Typography><Typography variant="h6">Manual interpretation needed</Typography></Box>}
    </Stack>
    <Alert severity="warning" icon={false} sx={{mx: 3, mb: 2.5, py: 0, bgcolor: '#faf6eb', color: '#80632c', borderRadius: 1.5}}>
      <Typography variant="body2">{review.reason}</Typography>
    </Alert>
    <Divider/>
    <Accordion disableGutters elevation={0} sx={{borderRadius: '0 !important'}}>
      <AccordionSummary expandIcon={<ExpandMoreRounded/>} id={`source-${review.id}`} aria-controls={`evidence-${review.id}`} sx={{px: 3}}>
        <Typography variant="body2" sx={{fontWeight: 550}}>View source &amp; full transcript</Typography>
      </AccordionSummary>
      <AccordionDetails id={`evidence-${review.id}`} sx={{px: 3, pb: 3}}>
        <Box component="blockquote" sx={{m: 0, mb: 2, p: 2.5, borderLeft: '3px solid #a8bc9d', bgcolor: '#f5f8f3', borderRadius: '0 8px 8px 0'}}>
          <Typography variant="body1" sx={{fontFamily: 'Georgia, serif', lineHeight: 1.8, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{review.excerpt}</Typography>
        </Box>
        <Stack spacing={0.5}>
          <Typography variant="caption" color="text.secondary">Speaker: {speaker.name || speaker.email || 'Not recorded'}</Typography>
          <Typography variant="caption" color="text.secondary">Source: {segment.start_time || 'Time not recorded'}{segment.end_time && ` → ${segment.end_time}`} · Passage {review.segment_index + 1}</Typography>
          {!!review.mentioned_employers?.length && <Typography variant="caption" color="text.secondary">Employers mentioned: {review.mentioned_employers.join(', ')}</Typography>}
          <Typography variant="caption" color="text.secondary">{attribution ? `LLM attribution · ${attribution.model} · ${attribution.version}` : 'Offline rules / legacy record'} · Employer ownership is unconfirmed.</Typography>
        </Stack>
        {!!snapshot.evidence?.length && <EvidenceLines title="Evidence cited by the model" lines={snapshot.evidence}/>}
        {transcript.origin === 'latest' && <Alert severity="info" sx={{mt: 3}}>
          This older review did not save a complete transcript. Showing the latest stored meeting transcript; it may differ from the original reviewed version.
        </Alert>}
        {transcript.segments.length ? <EvidenceLines title={`Full transcript · ${transcript.segments.length} passages`} lines={transcript.segments} flaggedIndex={transcript.flagged_index}/> : <>
          <Typography variant="body2" color="text.secondary" sx={{mt: 3}}>The full transcript is unavailable for this older record.</Typography>
          {!!context.length && <EvidenceLines title="Saved surrounding conversation" lines={context} flaggedIndex={review.segment_index}/>}
        </>}
      </AccordionDetails>
    </Accordion>
    {customerKey && <><Divider/><CorrectionPanel review={review} customerKey={customerKey} csrfToken={csrfToken} onResolved={onResolved}/></>}
  </Card>;
}
