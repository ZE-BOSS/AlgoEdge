// Site settings — edit these, no build step needed.
//
// The API address is worked out from where the page is served: on the real
// domain it is api.<domain>; anywhere else (a laptop) it is the local backend.
window.AVQ = {
  api: /alphavantiqcapital\.com$/.test(location.hostname)
    ? 'https://api.alphavantiqcapital.com'
    : 'http://localhost:8000',
  app: /alphavantiqcapital\.com$/.test(location.hostname)
    ? 'https://app.alphavantiqcapital.com'
    : 'http://localhost:5174',
  email: 'invest@alphavantiqcapital.com',
  // Your registration / licence line exactly as your legal adviser worded it,
  // e.g. "Alphavantiq Capital Ltd, RC 1234567. Registered with …". Shown in the
  // footer; hidden while empty. Do not paraphrase a regulator's wording.
  regulatory: '',
};
