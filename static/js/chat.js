window.ResearchAssistant = (() => {
  let messages = [], busy = false, company = null, year = 2024;
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
    if(!messages.length) log.append(node('div','Start with a number. Then explore the story behind it.','empty-state'));
    messages.forEach(m=>{
      const article=node('article',null,'message '+m.role);
      article.append(node('div',m.role==='user'?'You':m.role==='error'?'Request failed':'Research assistant','message-label'));
      if(m.scope)article.append(node('small',m.scope,'muted'));
      const content=node('div');markdown(content,m.content||'');article.append(content);
      if(m.warning)article.append(node('small',m.warning,'muted'));
      if(m.sources?.length){
        const details=node('details'), summary=node('summary',`Sources (${m.sources.length})`);details.append(summary);
        m.sources.forEach(s=>{const entry=node('div',null,'source');
          const link=node('a',s.document+(s.page?` · page ${s.page}`:' · page unavailable'));
          link.href='/api/source/'+encodeURIComponent(s.document)+(s.page?'#page='+s.page:'');link.target='_blank';link.rel='noopener';
          entry.append(link,node('p',s.snippet));details.append(entry);});article.append(details);
      }
      if(m.role==='assistant'&&m.content){const copy=node('button','Copy answer');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(m.content);copy.textContent='Copied';}catch{copy.textContent='Copy unavailable';}};article.append(copy);}
      if(m.role==='error'&&m.retry){const retry=node('button','Retry');retry.type='button';retry.disabled=busy;retry.onclick=()=>submit(m.retry.question,m.retry);article.append(retry);}
      log.append(article);
    });
    log.scrollTop=log.scrollHeight;
  }
  function setBusy(value){busy=value;$('askBtn').disabled=value;$('chatInput').disabled=value;$('clearChatBtn').disabled=value;
    $('chatLog').setAttribute('aria-busy',String(value));$('chatStatus').textContent=value?'Checking reports and metrics…':'';
    document.querySelectorAll('#chatSuggestions button').forEach(b=>b.disabled=value);
  }
  function suggestions(items){const box=$('chatSuggestions');box.replaceChildren();
    (items||[`What was ${company||'Apple'} revenue?`,`What were ${company||'Apple'} risks?`,'Compare Apple vs Microsoft operating margin']).forEach(q=>{const b=node('button',q);b.type='button';b.disabled=busy;b.onclick=()=>submit(q);box.append(b);});}
  async function submit(question,retryBody){
    if(busy||!question.trim())return;
    if(question.trim().length>1000){$('chatStatus').textContent='Use no more than 1,000 characters.';return;}
    const body=retryBody||{question:question.trim(),company,year,scope:$('chatScope')?.value||'company',history:messages.filter(m=>m.role==='user'||m.role==='assistant').slice(-12).map(m=>({role:m.role,content:m.content}))};
    messages.push({role:'user',content:question});const reply={role:'assistant',content:''};messages.push(reply);
    $('chatInput').value='';setBusy(true);render();
    try {await API.stream(body,event=>{
      if(event.type==='delta'){reply.content+=event.text;render();}
      if(event.type==='done'){Object.assign(reply,{content:event.answer,sources:event.sources,scope:event.scope,warning:event.warning});suggestions(event.suggestions);render();}
      if(event.type==='status')$('chatStatus').textContent=event.message;
    });}catch(e){messages.splice(messages.indexOf(reply),1);messages.push({role:'error',content:API.describeError(e),retry:e.retryable===false?null:body});}
    finally{setBusy(false);render();persist();$('chatInput').focus();}
  }
  function setScope(c,y){company=c;year=y||2024;$('chatScopeChip').textContent=(c||'All companies')+' · FY'+year;suggestions();}
  function init(){
    try{const stored=JSON.parse(sessionStorage.getItem('ip.conversation')||'[]');if(Array.isArray(stored))messages=stored.filter(m=>['user','assistant','error'].includes(m.role)&&typeof m.content==='string').slice(-40);}catch{}
    $('chatForm').onsubmit=e=>{e.preventDefault();submit($('chatInput').value);};
    $('chatInput').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit(e.target.value);}};
    $('clearChatBtn').onclick=()=>{messages=[];persist();render();suggestions();};
    if(!$('chatScope')){const select=node('select');select.id='chatScope';select.setAttribute('aria-label','Chat scope');
      [['company','This company'],['all','Compare all'],['none','No scope']].forEach(([v,l])=>{const option=node('option',l);option.value=v;select.append(option);});$('chatScopeChip').parentElement.append(select);}
    $('chatInput').maxLength=1000;setBusy(false);render();suggestions();
  }
  return {init,setScope,submit};
})();
