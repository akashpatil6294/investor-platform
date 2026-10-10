/* Same-origin API transport. Never place provider credentials in the browser. */
window.API = (() => {
  const timeout = 60000;
  function error(payload, status) {
    const detail = payload?.detail;
    return Object.assign(new Error(payload?.message || (typeof detail === 'string' ? detail : null) || `Request failed (${status}).`),
      {code: payload?.code || (status === 503 ? 'unavailable' : 'http'), status, retryable: payload?.retryable !== false});
  }
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || timeout);
    try {
      const response = await fetch(path, {...options, signal: controller.signal,
        headers: options.body instanceof FormData ? {} : {'Content-Type':'application/json'}});
      const payload = await response.json();
      if (!response.ok && !options.acceptError) throw error(payload, response.status);
      return payload;
    } catch (e) {
      if (e.name === 'AbortError') throw Object.assign(new Error('The request exceeded 60 seconds. Please retry.'), {code:'timeout', retryable:true});
      if (e instanceof TypeError) throw Object.assign(new Error('Cannot connect to the server. Check your connection and retry.'), {code:'network', retryable:true});
      throw e;
    } finally { clearTimeout(timer); }
  }
  async function stream(body, onEvent, signal) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    if (signal) signal.addEventListener('abort', () => controller.abort(), {once:true});
    let received = false;
    try {
      const response = await fetch('/api/chat/stream', {method:'POST',headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal:controller.signal});
      if (response.status === 404 || response.status === 405 || !response.body) {
        const data = await request('/api/chat', {method:'POST', body:JSON.stringify(body)});
        onEvent({type:'done', ...data}); return;
      }
      if (!response.ok) throw error(await response.json(),response.status);
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = '', done = false;
      while (!done) {
        const part = await reader.read();
        if (part.done) break;
        buffer += decoder.decode(part.value, {stream:true});
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0,boundary); buffer = buffer.slice(boundary+2);
          const line = frame.split('\n').find(s => s.startsWith('data: '));
          if (!line) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === 'error') throw error(event, 503);
          received = true; onEvent(event);
          if (event.type === 'done') done = true;
        }
      }
      if (!done) throw Object.assign(new Error('The answer stream ended early. Please retry.'), {code:'stream_interrupted', retryable:true});
    } catch (e) {
      if (e.name === 'AbortError') throw Object.assign(new Error('Answer stopped.'), {code:'stopped', retryable:true});
      if (e instanceof TypeError) throw Object.assign(new Error(received ? 'Connection lost during the answer. Please retry.' : 'Cannot connect to the assistant. Please retry.'), {code:'network',retryable:true});
      throw e;
    } finally { clearTimeout(timer); }
  }
  const DESCRIPTIONS = {
    rate_limited: 'Too many questions right now. Wait a moment and retry.',
    model_busy: 'The model is busy. Retry in a moment.',
    model_rate_limited: 'The model provider is rate limiting. Retry shortly.',
    timeout: 'The assistant took too long. Please retry.',
    not_configured: 'The assistant has no working model configured. Check the server settings.',
    stream_interrupted: 'The answer was interrupted. Retry for a complete answer.',
    network: 'Cannot reach the server. Check that the API is running.',
    stopped: 'Answer stopped.',
  };
  return {request, stream,
    getHealth:()=>request('/api/ready',{acceptError:true,timeout:10000}),
    getCompanies:async()=>{const p=await request('/api/companies');return {companies:p.companies||[]};},
    getMetrics:async()=>{const p=await request('/api/metrics');return {rows:p.data||[]};},
    sendFeedback:async(payload)=>request('/api/chat/feedback',{method:'POST',body:JSON.stringify(payload)}),
    describeError:e=>DESCRIPTIONS[e.code]||e.message||'Something went wrong. Please retry.'};
})();
