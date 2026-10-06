Cold API startup stalls importing sentence-transformers through the KPI module; first browser metrics request timed out, later health/metrics probes returned 200 (0.05s/0.91s).
Health checks no dependencies; the synchronous chat handler runs in a worker, so global event-loop blocking by chat is not established.
Chat disables fallback/retry, uses a small completion budget, and returns failures as HTTP 200; browser reproduction confirmed Gemini 503 UNAVAILABLE (high demand) with no retry.
Retrieval permits an explicit cross-company fallback; served dashboard.html says AI Analyst and chat.js targets a nonexistent suggestions ID.
No Git repository exists in the supplied directory; phase commits cannot be made without initializing one.
