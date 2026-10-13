// config.js
// Uses the local backend when the page is served from localhost,
// and PROD_API_BASE everywhere else (e.g. on Vercel).

// render link
const PROD_API_BASE = "https://bella-vista-api.onrender.com";

const IS_LOCAL = window.location.hostname === "localhost" ||
                 window.location.hostname === "127.0.0.1";

const API_BASE = IS_LOCAL ? "http://localhost:8001" : PROD_API_BASE;
