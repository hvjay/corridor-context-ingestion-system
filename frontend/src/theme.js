import {createTheme} from '@mui/material/styles';

export const theme = createTheme({
  palette: {
    primary: {main: '#315c49', dark: '#203f32', light: '#eaf1e9'},
    background: {default: '#f6f8f6', paper: '#ffffff'},
    text: {primary: '#243b32', secondary: '#6c7c73'},
    divider: '#e2e9e3', warning: {main: '#997226'},
  },
  typography: {
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h4: {fontWeight: 650, letterSpacing: '-0.8px'},
    h6: {fontWeight: 600, fontSize: '1.05rem'},
    body2: {lineHeight: 1.65},
    button: {textTransform: 'none', fontWeight: 600},
    overline: {fontSize: '0.65rem', fontWeight: 650, letterSpacing: '1.3px'},
  },
  shape: {borderRadius: 12},
  components: {
    MuiPaper: {defaultProps: {elevation: 0}},
    MuiButton: {defaultProps: {disableElevation: true}, styleOverrides: {root: {borderRadius: 8}}},
    MuiChip: {styleOverrides: {root: {borderRadius: 6}, sizeSmall: {fontSize: '0.7rem'}}},
    MuiAccordion: {styleOverrides: {root: {'&::before': {display: 'none'}}}},
    MuiListItemButton: {styleOverrides: {root: {borderRadius: 8, '&.Mui-selected': {backgroundColor: '#eaf1e9'}}}},
  },
});
