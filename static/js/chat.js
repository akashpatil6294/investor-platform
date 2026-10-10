window.ResearchAssistant = (() => {
  let messages = [], busy = false, company = null, year = null;
  const $ = id => document.getElementById(id);
  const node = (tag,text,cls) => {const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
  function persist(){try{sessionStorage.setItem('ip.conversation',JSON.stringify(messages.slice(-40)));}catch{}}
  function markdown(target,text){
    target.replaceChildren();
    for(const line of text.split('\n')) {
      const p=node('p');
      for(const [i,part] of line.split('**').entries()) p.append(node(i%2?'strong':'span',part));
      target.append(p);
    }
  }
  function render(){
    const log=$('chatLog');log.replaceChildren();
    if(!messages.length) log.append(node('div','Start with a reported number. Then explore the story behind it.','empty-state'));
    messages.forEach(m=>{
      const article=node('article',null,'message '+m.role);
      article.append(node('div',m.role==='user'?'You':m.role==='error'?'Request failed':'Research assistant','message-label'));
      if(m.answer_source)article.append(node('strong',m.answer_source,'source-badge'));
      if(m.scope)article.append(node('small',m.scope,'muted'));
      const content=node('div');markdown(content,m.content||'');article.append(content);
      if(m.warning)article.append(node('small',m.warning,'warning-note'));
      if(m.sources?.length){
        const details=node('details'), summary=node('summary',`Sources (${m.sources.length})`);details.append(summary);
        m.sources.forEach(s=>{const entry=node('div',null,'source');
          const link=node('a',s.document+(s.page?` · page ${s.page}`:s.url?' · web':''));
          link.href=s.url&&/^https?:\/\//i.test(s.url)?s.url:'/api/source/'+encodeURIComponent(s.document)+(s.page?'#page='+s.page:'');link.target='_blank';link.rel='noopener';
          entry.append(link,node('p',s.snippet));details.append(entry);});
        article.append(details);
      }
      if(m.answer_source==='Web'&&m.company&&m.year&&m.sources?.some(s=>s.url)){
        const save=node('button','Add this value to the metrics (source: web)');save.onclick=()=>{
          const d=node('dialog',null,'review-dialog'),form=node('form'),title=node('h2','Review web value before saving');d.append(title,node('p',m.content));
          const controls={};[['metric','Metric: revenue, net_income, operating_income, cash_flow, total_assets, total_liabilities','revenue'],['value','Value as reported',''],['currency','Currency (three-letter code)',''],['unit','Unit: unit, thousand, lakh, crore, million, billion',''],['url','Source URL',m.sources.find(s=>s.url).url]].forEach(([k,l,v])=>{const label=node('label',l),i=node('input');i.value=v;i.required=true;if(k==='value'){i.type='number';i.step='any';}controls[k]=i;label.append(i);form.append(label);});
          const confirm=node('button','Save reviewed web value'),close=node('button','Cancel'),error=node('p');confirm.type='submit';close.type='button';close.onclick=()=>d.close();form.append(confirm,close,error);d.append(form);document.body.append(d);d.showModal();d.onclose=()=>d.remove();
          form.onsubmit=async e=>{e.preventDefault();confirm.disabled=true;try{const body=Object.fromEntries(Object.entries(controls).map(([k,i])=>[k,k==='value'?Number(i.value):i.value]));await API.request(`/api/metrics/${encodeURIComponent(m.company)}/${m.year}`,{method:'PATCH',body:JSON.stringify({...body,source:'web',quote:m.content.slice(0,400)})});d.close();window.dispatchEvent(new Event('metrics-updated'));save.textContent='Web value saved';}catch(err){error.textContent=err.message;confirm.disabled=false;}};
        };article.append(save);
      }
      if(m.role==='assistant'&&m.content){
        const actions=node('div',null,'message-actions');
        const copy=node('button','Copy answer');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(m.content);copy.textContent='Copied';}catch{copy.textContent='Copy unavailable';}};actions.append(copy);
        if(!m.feedbackGiven){
          const up=node('button','Helpful');up.type='button';up.onclick=()=>sendFeedback(m,true,up);
          const down=node('button','Not helpful');down.type='button';down.onclick=()=>sendFeedback(m,false,down);
          actions.append(up,down);
        } else actions.append(node('span',m.feedbackGiven==='helpful'?'Marked helpful':'Marked not helpful','muted'));
        article.append(actions);
      }
      if(m.role==='error'&&m.retry){const retry=node('button','Retry');retry.type='button';retry.disabled=busy;retry.onclick=()=>submit(m.retry.question,m.retry);article.append(retry);}
      log.append(article);
    });
    log.scrollTop=log.scrollHeight;
  }
  async function sendFeedback(m,helpful,button){
    button.disabled=true;
    try{await API.sendFeedback({question:m.question||'',answer_source:m.answer_source,helpful});m.feedbackGiven=helpful?'helpful':'not_helpful';render();}
    catch(e){button.disabled=false;$('chatStatus').textContent=API.describeError(e);}
  }
  function setBusy(value){busy=value;$('askBtn').disabled=value;$('chatInput').disabled=value;$('clearChatBtn').disabled=value;
    $('stopBtn').hidden=!value;
    $('chatLog').setAttribute('aria-busy',String(value));$('chatStatus').textContent=value?'Checking reports and metrics…':'';
    document.querySelectorAll('#chatSuggestions button').forEach(b=>b.disabled=value);
  }
  function suggestions(items){const box=$('chatSuggestions');box.replaceChildren();
    (items||[`What was ${company||'this company'} revenue?`,`What risks does ${company||'the report'} identify?`,'How did revenue change year over year?']).forEach(q=>{const b=node('button',q);b.type='button';b.disabled=busy;b.onclick=()=>submit(q);box.append(b);});}
  async function submit(question,retryBody){
    if(busy||!question.trim())return;
    if(question.trim().length>1000){$('chatStatus').textContent='Use no more than 1,000 characters.';return;}
    const body=retryBody||{question:question.trim(),company,year,scope:$('chatScope')?.value||'company',history:messages.filter(m=>m.role==='user'||m.role==='assistant').slice(-12).map(m=>({role:m.role,content:m.content}))};
    messages.push({role:'user',content:question});const reply={role:'assistant',content:'',question:question.trim()};messages.push(reply);
    $('chatInput').value='';setBusy(true);render();
    const controller=new AbortController();stopController=controller;
    try {await API.stream(body,event=>{
      if(event.type==='delta'){reply.content+=event.text;render();}
      if(event.type==='done'){Object.assign(reply,{content:event.answer,sources:event.sources,scope:event.scope,warning:event.warning,answer_source:event.answer_source,company:event.company,year:event.year});suggestions(event.suggestions);render();}
      if(event.type==='status')$('chatStatus').textContent=event.message;
    },controller.signal);}
    catch(e){
      if(e.code==='stopped'){reply.content=reply.content||'(stopped)';}
      else{messages.splice(messages.indexOf(reply),1);messages.push({role:'error',content:API.describeError(e),retry:e.retryable===false?null:body});}
    }
    finally{setBusy(false);render();persist();$('chatInput').focus();}
  }
  let stopController=null;
  function stop(){if(stopController)stopController.abort();}
  function setScope(c,y){company=c;year=y;$('chatScopeChip').textContent=(c||'All companies')+(y?' · FY'+y:'');suggestions();}
  function init(){
    try{const stored=JSON.parse(sessionStorage.getItem('ip.conversation')||'[]');if(Array.isArray(stored))messages=stored.filter(m=>['user','assistant','error'].includes(m.role)&&typeof m.content==='string').slice(-40);}catch{}
    $('chatForm').onsubmit=e=>{e.preventDefault();submit($('chatInput').value);};
    $('chatInput').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit($('chatInput').value);}};
    $('stopBtn').onclick=stop;
    $('clearChatBtn').onclick=()=>{messages=[];persist();render();suggestions();};
    if(!$('chatScope')){const select=node('select');select.id='chatScope';select.setAttribute('aria-label','Chat scope');
      [['company','This company'],['all','Compare all'],['none','No scope']].forEach(([v,l])=>{const option=node('option',l);option.value=v;select.append(option);});$('chatScopeChip').parentElement.append(select);}
    $('chatInput').maxLength=1000;setBusy(false);render();suggestions();
  }
  return {init,setScope,submit,stop};
})();
