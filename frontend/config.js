// config.js
// Uses the local backend when the page is served from localhost,
// and PROD_API_BASE everywhere else (e.g. on Vercel).

// TODO: after deploying the backend to Render, put its URL here (no trailing slash).
const PROD_API_BASE = "https://YOUR-SERVICE-NAME.onrender.com";

const IS_LOCAL = window.location.hostname === "localhost" ||
                 window.location.hostname === "127.0.0.1";

const API_BASE = IS_LOCAL ? "http://localhost:8001" : PROD_API_BASE;
